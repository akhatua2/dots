"""Standalone inference loop for a loaded mlx-lm model.

`FullModelHost` wraps a model + tokenizer and exposes one async method:
`run_turn(...)`. Workers in the new architecture own a `FullModelHost`
and serve `delegate_turn` requests from the hub by routing through it.

Per-conversation KV caches live here. `reset_conversation(conv_id)`
drops the cache; the next prefill rebuilds it.
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable

import mlx.core as mx
from mlx_lm.models.base import create_attention_mask  # noqa: F401 — monkeypatched in tests
from mlx_lm.models.cache import make_prompt_cache  # noqa: F401 — monkeypatched in tests


def _sample(logits_row, temperature: float) -> int:
    if temperature <= 0:
        return int(mx.argmax(logits_row, axis=-1).item())
    return int(mx.random.categorical(logits_row / temperature).item())


def _stop_ids(tokenizer) -> set[int]:
    ids: set[int] = set()
    if getattr(tokenizer, "eos_token_id", None) is not None:
        ids.add(int(tokenizer.eos_token_id))
    for marker in ("<|im_end|>", "<|endoftext|>", "<|eot_id|>"):
        try:
            tid = tokenizer.convert_tokens_to_ids(marker)
            if isinstance(tid, int) and tid > 0:
                ids.add(tid)
        except Exception:
            pass
    return ids


def _active_params(model) -> int:
    """Total active params, for FLOP-CU attribution. Returns 0 if the
    model object isn't introspectable (test fakes)."""
    try:
        from mlx.utils import tree_flatten
        total = 0
        for _, v in tree_flatten(model.parameters()):
            if hasattr(v, "size"):
                total += int(v.size)
        return total
    except Exception:
        return 0


class FullModelHost:
    """Owns a loaded model + tokenizer and serves chat turns.

    Per-conversation KV caches are tracked here. The worker process holds
    one of these for the duration of its connection to the hub.
    """

    def __init__(self, model, tokenizer):
        self.model = model
        self.tokenizer = tokenizer
        self.n_layers = len(model.model.layers)
        self.active_params = _active_params(model)
        self._caches: dict[str, list] = {}
        # Tokens already advanced through the cache for each conversation.
        # Next turn's prefill skips the prefix this number long, so a
        # multi-turn chat doesn't re-prefill the whole history every turn.
        self._token_counts: dict[str, int] = {}

    def reset_conversation(self, conv_id: str) -> None:
        self._caches.pop(conv_id, None)
        self._token_counts.pop(conv_id, None)

    def _get_cache(self, conv_id: str):
        cache = self._caches.get(conv_id)
        if cache is None:
            cache = make_prompt_cache(self.model)
            self._caches[conv_id] = cache
        return cache

    def _project_logits(self, hidden_last):
        if self.model.args.tie_word_embeddings:
            return self.model.model.embed_tokens.as_linear(hidden_last)
        return self.model.lm_head(hidden_last)

    def _forward(self, ids_arr, cache):
        hidden = self.model.model.embed_tokens(ids_arr)
        hidden = self._forward_layers(hidden, cache, 0, self.n_layers)
        hidden = self.model.model.norm(hidden)
        return hidden

    def _forward_layers(self, hidden, cache, start: int, end: int):
        """Run a contiguous slice of decoder layers `[start, end)` on
        `hidden`. The cache slice tracks the per-layer KV state for the
        same range.

        Multi-worker pipeline calls this to run *only* the layers
        assigned to this worker, with the caller handling embedding /
        final norm / sampling.
        """
        cache_slice = cache[start:end]
        mask = create_attention_mask(hidden, cache_slice)
        for offset, layer in enumerate(self.model.model.layers[start:end]):
            hidden = layer(hidden, mask=mask, cache=cache_slice[offset])
        return hidden

    def forward_segment(self, conv_id: str, hidden, start: int, end: int):
        """Run `hidden` through layer range `[start, end)` for `conv_id`.

        Used by workers that are *chain* members (not head) in a
        multi-worker placement. They neither embed nor sample — they
        just forward through their layer slice.
        """
        cache = self._get_cache(conv_id)
        return self._forward_layers(hidden, cache, start, end)

    async def run_turn(
        self,
        conv_id: str,
        history: list[dict],
        *,
        max_tokens: int = 256,
        temperature: float = 0.7,
        on_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[str, dict]:
        """One chat turn. Streams generated tokens through `on_token`.

        Only the *new* portion of `history` (past whatever was prefilled
        on prior turns for this conv_id) is run through prefill. The KV
        cache holds everything from earlier turns, so a long-running
        conversation's per-turn prefill stays tiny.
        """
        cache = self._get_cache(conv_id)
        full_ids = self.tokenizer.apply_chat_template(
            history, add_generation_prompt=True,
        )
        already_seen = self._token_counts.get(conv_id, 0)
        if already_seen > len(full_ids):
            # Cache is ahead of the template — chat history was trimmed
            # or rewritten. Best to reset.
            self.reset_conversation(conv_id)
            cache = self._get_cache(conv_id)
            already_seen = 0
        new_ids = full_ids[already_seen:]
        if not new_ids:
            # Nothing new to prefill. Caller asked us to generate from a
            # stale conversation; return empty.
            return "", {
                "prefill_tokens": 0,
                "decode_tokens": 0,
                "prefill_ms": 0.0,
                "elapsed_ms": 0.0,
            }

        ids_arr = mx.array([new_ids])

        # Prefill (only the new tail).
        t0 = time.perf_counter()
        hidden = self._forward(ids_arr, cache)
        logits = self._project_logits(hidden[:, -1:, :])
        token = _sample(logits[0, -1], temperature)
        prefill_tokens = len(new_ids)
        prefill_ms = (time.perf_counter() - t0) * 1000
        # Bump token_count by the number of tokens we just pushed through.
        self._token_counts[conv_id] = already_seen + len(new_ids)

        # Decode loop.
        stop_ids = _stop_ids(self.tokenizer)
        response_tokens: list[int] = []
        decode_start = time.perf_counter()

        while len(response_tokens) < max_tokens and token not in stop_ids:
            text = self.tokenizer.decode([token])
            if on_token is not None:
                await on_token(text)
            response_tokens.append(token)
            next_in = mx.array([[token]])
            hidden = self._forward(next_in, cache)
            logits = self._project_logits(hidden[:, -1:, :])
            token = _sample(logits[0, -1], temperature)
            self._token_counts[conv_id] += 1

        elapsed_ms = (time.perf_counter() - decode_start) * 1000
        text = self.tokenizer.decode(response_tokens)
        return text, {
            "prefill_tokens": prefill_tokens,
            "decode_tokens": len(response_tokens),
            "prefill_ms": prefill_ms,
            "elapsed_ms": elapsed_ms,
        }

"""Single-machine compute plane.

When the model fits on the querier's machine (the self-utilization case),
or when the application explicitly wants to skip the pool, this is the
plane to use. The entire inference loop runs in-process.
"""
from __future__ import annotations

import time

from .base import ComputePlane, GenerationStats, VirtualResources


def _sample(logits_row, temperature: float) -> int:
    import mlx.core as mx
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


class LocalComputePlane(ComputePlane):
    """Single-machine plane.

    Loads the full model in-process. Inference is a tight async loop —
    no IPC, no network, no scheduler. The "self-utilization wins" case
    collapses to this: if the querier's machine can hold the model, this
    is the plane we run on.
    """

    def __init__(self, model_id: str):
        from mlx_lm import load as mlx_load
        self.model_id = model_id
        self.model, self.tokenizer = mlx_load(model_id)
        self.n_layers = len(self.model.model.layers)
        self._caches: dict[str, list] = {}
        self._closed = False

    @property
    def virtual_resources(self) -> VirtualResources:
        from ..util import (
            effective_tflops_estimate, free_ram_gb, supported_backends,
        )
        return VirtualResources(
            ram_gb_available=free_ram_gb(),
            compute_tflops_per_sec=effective_tflops_estimate(),
            backends=supported_backends(),
            n_workers=1,
        )

    def _get_cache(self, conv_id: str):
        from mlx_lm.models.cache import make_prompt_cache
        cache = self._caches.get(conv_id)
        if cache is None:
            cache = make_prompt_cache(self.model)
            self._caches[conv_id] = cache
        return cache

    async def reset_conversation(self, conv_id: str) -> None:
        self._caches.pop(conv_id, None)

    async def shutdown(self) -> None:
        self._closed = True
        self._caches.clear()

    async def generate(
        self,
        history: list[dict],
        *,
        max_tokens: int = 256,
        temperature: float = 0.7,
        on_token=None,
        conv_id: str = "default",
    ) -> tuple[str, GenerationStats]:
        import mlx.core as mx
        from mlx_lm.models.base import create_attention_mask

        if self._closed:
            raise RuntimeError("plane has been shut down")

        cache = self._get_cache(conv_id)
        prompt_ids = self.tokenizer.apply_chat_template(
            history, add_generation_prompt=True,
        )
        ids_arr = mx.array([prompt_ids])

        # Prefill.
        hidden = self.model.model.embed_tokens(ids_arr)
        mask = create_attention_mask(hidden, cache)
        for i, layer in enumerate(self.model.model.layers):
            hidden = layer(hidden, mask=mask, cache=cache[i])
        hidden = self.model.model.norm(hidden)
        logits = (
            self.model.model.embed_tokens.as_linear(hidden[:, -1:, :])
            if self.model.args.tie_word_embeddings
            else self.model.lm_head(hidden[:, -1:, :])
        )
        token = _sample(logits[0, -1], temperature)
        prefill_tokens = len(prompt_ids)
        decode_start = time.perf_counter()

        # Decode.
        stop_ids = _stop_ids(self.tokenizer)
        response_tokens: list[int] = []
        while len(response_tokens) < max_tokens and token not in stop_ids:
            text = self.tokenizer.decode([token])
            if on_token is not None:
                await on_token(text)
            response_tokens.append(token)
            next_in = mx.array([[token]])
            hidden = self.model.model.embed_tokens(next_in)
            mask = create_attention_mask(hidden, cache)
            for i, layer in enumerate(self.model.model.layers):
                hidden = layer(hidden, mask=mask, cache=cache[i])
            hidden = self.model.model.norm(hidden)
            logits = (
                self.model.model.embed_tokens.as_linear(hidden[:, -1:, :])
                if self.model.args.tie_word_embeddings
                else self.model.lm_head(hidden[:, -1:, :])
            )
            token = _sample(logits[0, -1], temperature)

        elapsed_ms = (time.perf_counter() - decode_start) * 1000
        text = self.tokenizer.decode(response_tokens)
        stats = GenerationStats(
            prefill_tokens=prefill_tokens,
            decode_tokens=len(response_tokens),
            elapsed_ms=elapsed_ms,
            placement_summary=f"local·{self.n_layers}L",
        )
        return text, stats

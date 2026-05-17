"""Model shard: loads a full mlx-lm model but only runs an assigned layer range.

For the PoC we accept the memory cost of loading the whole model on every node
(SmolLM2-135M is ~70 MB, Qwen2.5-0.5B is ~300 MB; negligible). A future version
can stream and keep only the assigned weights resident.

Each shard holds one KV cache *per conversation id*. Two users chatting through
the mesh keep separate histories, and a conversation's cache persists across
turns so we only prefill the new tokens each round.
"""
from __future__ import annotations

from dataclasses import dataclass

import mlx.core as mx
from mlx_lm import load as mlx_load
from mlx_lm.models.base import create_attention_mask
from mlx_lm.models.cache import make_prompt_cache


@dataclass
class ShardSpec:
    model_id: str
    layer_start: int  # inclusive
    layer_end: int    # exclusive


class Shard:
    def __init__(self, spec: ShardSpec):
        self.spec = spec
        self.model, self.tokenizer = mlx_load(spec.model_id)
        self.n_layers = len(self.model.model.layers)
        if spec.layer_end > self.n_layers:
            raise ValueError(
                f"layer_end={spec.layer_end} exceeds model layers={self.n_layers}"
            )
        # One KV cache list per active conversation. Created lazily on first use.
        self._caches: dict[str, list] = {}

    def _cache_for(self, conv_id: str) -> list:
        cache = self._caches.get(conv_id)
        if cache is None:
            cache = make_prompt_cache(self.model)
            self._caches[conv_id] = cache
        return cache

    def reset_conversation(self, conv_id: str) -> None:
        self._caches.pop(conv_id, None)

    def known_conversations(self) -> list[str]:
        return list(self._caches.keys())

    def embed(self, tokens: mx.array) -> mx.array:
        # tokens: [batch, seq] int -> [batch, seq, hidden]
        return self.model.model.embed_tokens(tokens)

    def run_layers(self, conv_id: str, hidden: mx.array) -> mx.array:
        """Run hidden through this shard's layer range, updating the
        per-conversation KV cache."""
        full_cache = self._cache_for(conv_id)
        # Slice the cache to the layers we own so the mask's offset is
        # computed from the right entries.
        cache_slice = [full_cache[i] for i in
                       range(self.spec.layer_start, self.spec.layer_end)]
        mask = create_attention_mask(hidden, cache_slice)
        for idx, i in enumerate(range(self.spec.layer_start, self.spec.layer_end)):
            hidden = self.model.model.layers[i](
                hidden, mask=mask, cache=cache_slice[idx]
            )
        return hidden

    def head(self, hidden: mx.array) -> mx.array:
        """Apply final norm + lm_head -> logits."""
        normed = self.model.model.norm(hidden)
        if self.model.args.tie_word_embeddings:
            return self.model.model.embed_tokens.as_linear(normed)
        return self.model.lm_head(normed)

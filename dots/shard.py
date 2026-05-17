"""Model shard: loads a full mlx-lm model but only runs an assigned layer range.

For the PoC we accept the memory cost of loading the whole model on every node
(SmolLM2-135M is ~70 MB; this is negligible). A future version can stream and
keep only the assigned weights resident.
"""
from __future__ import annotations

from dataclasses import dataclass

import mlx.core as mx
from mlx_lm import load as mlx_load
from mlx_lm.models.cache import make_prompt_cache


@dataclass
class ShardSpec:
    model_id: str
    layer_start: int  # inclusive
    layer_end: int    # exclusive
    is_first: bool    # owns embed_tokens
    is_last: bool     # owns final norm + lm_head


class Shard:
    def __init__(self, spec: ShardSpec):
        self.spec = spec
        self.model, self.tokenizer = mlx_load(spec.model_id)
        self.n_layers = len(self.model.model.layers)
        if spec.layer_end > self.n_layers:
            raise ValueError(
                f"layer_end={spec.layer_end} exceeds model layers={self.n_layers}"
            )
        # KV cache covers the full model; we just won't touch entries outside our range.
        self.cache = make_prompt_cache(self.model)

    def reset_kv(self) -> None:
        self.cache = make_prompt_cache(self.model)

    def embed(self, tokens: mx.array) -> mx.array:
        # tokens: [batch, seq] int. Returns [batch, seq, hidden].
        return self.model.model.embed_tokens(tokens)

    def run_layers(self, hidden: mx.array) -> mx.array:
        # hidden: [batch, seq, hidden]. Mutates self.cache for our layer range.
        for i in range(self.spec.layer_start, self.spec.layer_end):
            hidden = self.model.model.layers[i](hidden, cache=self.cache[i])
        return hidden

    def head(self, hidden: mx.array) -> mx.array:
        # Returns logits: [batch, seq, vocab].
        normed = self.model.model.norm(hidden)
        if self.model.args.tie_word_embeddings:
            return self.model.model.embed_tokens.as_linear(normed)
        return self.model.lm_head(normed)

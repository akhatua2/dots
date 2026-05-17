"""Model shard: loads an mlx-lm model and frees everything outside the
assigned layer range so a worker only retains the weights it actually
needs to execute.

After construction, a Shard holds in RAM:

  • The transformer blocks in [layer_start, layer_end)
  • The model's parameter-free scaffolding (config, layer-norm shapes, etc.)

It does NOT hold:

  • Transformer blocks outside [layer_start, layer_end)
  • embed_tokens / norm / lm_head — only the hub uses those for tokenizer
    operations and final sampling, never the worker.

Memory cost per worker drops from the full model to roughly
(layer_end - layer_start) / total_layers * total_weights. That's what
turns the `--max-ram` flag from a promise into an actual reduction.

Each shard holds one KV cache per conversation id. Two users chatting
through the mesh keep separate histories, and a conversation's cache
persists across turns so we only prefill the new tokens each round.
"""
from __future__ import annotations

import gc
from dataclasses import dataclass

import mlx.core as mx
import mlx.nn as nn
from mlx_lm import load as mlx_load
from mlx_lm.models.base import create_attention_mask
from mlx_lm.models.cache import make_prompt_cache


class _DroppedModule(nn.Module):
    """Placeholder that replaces a Module this Shard doesn't own. Holds zero
    parameters so the original module's weights are released once the
    reference is overwritten. Calling it is a bug — workers only run the
    layers inside their assigned range."""

    def __call__(self, *args, **kwargs):
        raise RuntimeError(
            "tried to invoke a Module that was dropped during shard construction"
        )


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
        # Drop weights we won't use. This is the one line that makes the
        # whole sharding story actually save memory.
        self._free_unowned_weights()
        # One KV cache list per active conversation. Created lazily on first use.
        self._caches: dict[str, list] = {}

    def _free_unowned_weights(self) -> None:
        """Replace any transformer block outside [layer_start, layer_end),
        plus the model's embed_tokens / norm / lm_head, with empty stubs
        so the underlying parameter arrays lose their last reference and
        MLX can release the memory."""
        start = self.spec.layer_start
        end = self.spec.layer_end
        inner = self.model.model

        # Drop transformer blocks outside our range.
        for i in range(self.n_layers):
            if not (start <= i < end):
                inner.layers[i] = _DroppedModule()

        # The hub owns embed / norm / lm_head; the worker never calls them.
        # Replace with stubs so MLX can free their tensors. Wrapped in try
        # so a model whose top-level Module hides these attributes a bit
        # differently doesn't break the load.
        for attr in ("embed_tokens", "norm"):
            if hasattr(inner, attr):
                try:
                    setattr(inner, attr, _DroppedModule())
                except (AttributeError, TypeError):
                    pass
        if hasattr(self.model, "lm_head") and self.model.lm_head is not None:
            try:
                self.model.lm_head = _DroppedModule()
            except (AttributeError, TypeError):
                pass

        # Give Python's GC + MLX a kick so the freed tensors don't linger
        # in cycles. The cost is tens of microseconds; the benefit is
        # megabytes-to-gigabytes back on a real model.
        gc.collect()

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
        # Kept for symmetry with old call sites; workers never invoke this
        # since embed_tokens was dropped during construction. The hub uses
        # its own model.model.embed_tokens directly.
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
        """Apply final norm + lm_head -> logits.

        Same caveat as `embed`: workers never call this — the hub owns
        sampling. Kept for parity in case a future code path needs it on
        the hub side via the same Shard abstraction."""
        normed = self.model.model.norm(hidden)
        if self.model.args.tie_word_embeddings:
            return self.model.model.embed_tokens.as_linear(normed)
        return self.model.lm_head(normed)

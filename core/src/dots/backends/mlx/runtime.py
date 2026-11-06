"""MLX backend runtime.

`MlxBackendRuntime` hides mlx-lm specifics behind the backend interface.
`FullModelHost` remains as the compatibility holder for the loaded model,
tokenizer, and per-conversation KV caches.

Per-conversation KV caches live here. `reset_conversation(conv_id)`
drops the cache; the next prefill rebuilds it.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Awaitable, Callable

from ..base import (
    BackendCapabilities,
    BackendRuntime,
    ForwardHidden,
    ModelAssignment,
    ModelMetadata,
    PrefillOutput,
    PreparationRequirement,
    PreparedModel,
    ProgressCallback,
    StreamDetokenizer,
)
from ...placement.model_metadata import (
    assignment_ram_gb,
    geometry_from_config,
    geometry_with_local_safetensors_metadata,
    geometry_with_safetensors_metadata,
    safetensors_disk_size_gb,
)
from ...placement.work_units import ModelGeometry
from .geometry import geometry_from_mlx_model
from .load_progress import monitor_download_progress


def create_attention_mask(*args, **kwargs):
    from mlx_lm.models.base import create_attention_mask as _real

    return _real(*args, **kwargs)


def make_prompt_cache(*args, **kwargs):
    from mlx_lm.models.cache import make_prompt_cache as _real

    return _real(*args, **kwargs)


def trim_prompt_cache(*args, **kwargs):
    from mlx_lm.models.cache import trim_prompt_cache as _real

    return _real(*args, **kwargs)


def load_partial(*args, **kwargs):
    from .partial_loader import load_partial as _real

    return _real(*args, **kwargs)


def array(*args, **kwargs):
    import mlx.core as mx

    return mx.array(*args, **kwargs)


def argmax_token(logits_row) -> int:
    import mlx.core as mx

    return int(mx.argmax(logits_row, axis=-1).item())


def categorical_token(logits_row, temperature: float) -> int:
    import mlx.core as mx

    return int(mx.random.categorical(logits_row / temperature).item())


def _apply_repetition_penalty(logits_row, recent_tokens, penalty: float):
    """HF-style repetition penalty: shrink the logit of any recently
    generated token toward zero (divide if positive, multiply if
    negative), lowering its probability of being picked again. No-op
    when disabled or there's no history yet."""
    if penalty == 1.0 or not recent_tokens:
        return logits_row
    import mlx.core as mx

    idx = mx.array(list({int(t) for t in recent_tokens}))
    selected = logits_row[idx]
    logits_row[idx] = mx.where(
        selected > 0, selected / penalty, selected * penalty
    )
    return logits_row


def _sample(
    logits_row,
    temperature: float,
    recent_tokens: list[int] | None = None,
    repetition_penalty: float = 1.0,
) -> int:
    logits_row = _apply_repetition_penalty(
        logits_row, recent_tokens, repetition_penalty
    )
    if temperature <= 0:
        return argmax_token(logits_row)
    return categorical_token(logits_row, temperature)


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

    def trim(self, conv_id: str, n: int) -> None:
        """Rolls the conversation's KV cache back by `n` tokens (a draft's rejected tail)."""
        if n <= 0:
            return
        cache = self._caches.get(conv_id)
        if cache is not None:
            trim_prompt_cache(cache, n)
        self._token_counts[conv_id] = max(0, self._token_counts.get(conv_id, 0) - n)

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
        """Compatibility wrapper around the autoregressive strategy."""
        from ...generation.autoregressive import AutoregressiveGeneration
        from ...generation.base import GenerationRequest

        runtime = MlxBackendRuntime.from_host(self)
        result = await AutoregressiveGeneration().generate(
            GenerationRequest(
                runtime=runtime,
                conv_id=conv_id,
                history=history,
                max_tokens=max_tokens,
                temperature=temperature,
                on_token=on_token,
            )
        )
        return result.text, result.stats


class MlxBackendRuntime(BackendRuntime):
    capabilities = BackendCapabilities(
        name="mlx",
        model_formats=frozenset({"mlx-lm", "safetensors"}),
        supports_full_model=True,
        supports_partial_layers=True,
        supports_pipeline_segments=True,
    )

    def __init__(self):
        self.model_id: str | None = None
        self.assignment = ModelAssignment()
        self.host: FullModelHost | None = None
        self._geometry: ModelGeometry | None = None

    @classmethod
    def from_host(cls, host: FullModelHost) -> "MlxBackendRuntime":
        runtime = cls()
        runtime.host = host
        runtime._geometry = geometry_from_mlx_model(host.model)
        return runtime

    async def load(
        self,
        model_id: str,
        assignment: ModelAssignment,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        prepared = await self.prepare_model(model_id, assignment)
        monitor: asyncio.Task | None = None
        if progress_callback is not None:
            monitor = asyncio.create_task(
                monitor_download_progress(prepared.runtime_id, progress_callback)
            )
        try:
            model, tokenizer, _n_total = await asyncio.to_thread(
                self._load_partial,
                prepared.runtime_id,
                layer_start=assignment.layer_start,
                layer_end=assignment.layer_end,
                needs_head=assignment.needs_head,
            )
        finally:
            if monitor is not None:
                monitor.cancel()
                try:
                    await monitor
                except (asyncio.CancelledError, Exception):
                    pass

        self.model_id = prepared.source_id
        self.assignment = assignment
        self.host = FullModelHost(model, tokenizer)
        self._geometry = geometry_from_mlx_model(model)

    @staticmethod
    def _load_partial(*args, **kwargs):
        return load_partial(*args, **kwargs)

    def validate_model(self, model_id: str, assignment: ModelAssignment) -> None:
        if assignment.layer_start < 0:
            raise ValueError("layer_start must be non-negative")
        if (
            assignment.layer_end is not None
            and assignment.layer_end < assignment.layer_start
        ):
            raise ValueError("layer_end must be greater than or equal to layer_start")
        if not model_id:
            raise ValueError("model_id is required")

    async def prepare_model(
        self,
        model_id: str,
        assignment: ModelAssignment,
    ) -> PreparedModel:
        """Resolve/prepare a user model id for MLX loading.

        mlx-lm can already load many HF repos directly, including converted
        mlx-community repos. Future conversion from a standard HF checkpoint
        belongs here, not in Worker or compute-plane code.
        """
        self.validate_model(model_id, assignment)
        return PreparedModel(
            source_id=model_id,
            runtime_id=model_id,
            format="mlx-lm",
        )

    async def preparation_requirement(
        self,
        model_id: str,
        assignment: ModelAssignment,
    ) -> PreparationRequirement:
        self.validate_model(model_id, assignment)
        metadata = await self.metadata_for(model_id)
        disk_gb = await asyncio.to_thread(
            safetensors_disk_size_gb,
            metadata.runtime_id,
        )
        ram_gb = assignment_ram_gb(metadata.geometry, assignment)
        return PreparationRequirement(
            needs_conversion=False,
            disk_gb=disk_gb,
            ram_gb=ram_gb,
            reason="mlx-lm direct load path with exact safetensors metadata",
        )

    async def metadata_for(self, model_id: str) -> ModelMetadata:
        prepared = await self.prepare_model(model_id, ModelAssignment())
        try:
            from mlx_lm.utils import _download, load_config

            def _config_only(repo: str):
                return _download(
                    repo,
                    allow_patterns=[
                        "config.json",
                        "generation_config.json",
                        "tokenizer_config.json",
                    ],
                )

            local_model_path = Path(prepared.runtime_id)
            if local_model_path.is_dir():
                model_path = local_model_path
            else:
                model_path = await asyncio.to_thread(_config_only, prepared.runtime_id)
            cfg = await asyncio.to_thread(load_config, model_path)
        except Exception as e:
            raise RuntimeError(
                f"could not load model config for {model_id!r}: {e}"
            ) from e

        geom = geometry_from_config(cfg)
        if geom is None:
            raise RuntimeError(
                f"model {model_id!r} config is missing required geometry"
            )
        if Path(prepared.runtime_id).is_dir():
            geometry = await asyncio.to_thread(
                geometry_with_local_safetensors_metadata,
                prepared.runtime_id,
                geom,
            )
        else:
            geometry = await asyncio.to_thread(
                geometry_with_safetensors_metadata,
                prepared.runtime_id,
                geom,
            )
        return ModelMetadata(
            source_id=prepared.source_id,
            runtime_id=prepared.runtime_id,
            geometry=geometry,
        )

    def _require_host(self) -> FullModelHost:
        if self.host is None:
            raise RuntimeError("model not loaded")
        return self.host

    def reset_conversation(self, conv_id: str) -> None:
        self._require_host().reset_conversation(conv_id)

    async def _forward_hidden(
        self,
        conv_id: str,
        hidden,
        forward_hidden: ForwardHidden | None,
    ):
        host = self._require_host()
        if forward_hidden is not None:
            return await forward_hidden(hidden)
        return host.forward_segment(conv_id, hidden, 0, host.n_layers)

    async def prefill(
        self,
        conv_id: str,
        history: list[dict],
        *,
        forward_hidden: ForwardHidden | None = None,
    ) -> PrefillOutput:
        host = self._require_host()
        full_ids = host.tokenizer.apply_chat_template(
            history,
            add_generation_prompt=True,
        )
        already_seen = host._token_counts.get(conv_id, 0)
        if already_seen > len(full_ids):
            host.reset_conversation(conv_id)
            already_seen = 0
        new_ids = full_ids[already_seen:]
        if not new_ids:
            return PrefillOutput(None, 0, 0.0)

        t0 = time.perf_counter()
        hidden = host.model.model.embed_tokens(array([new_ids]))
        hidden = await self._forward_hidden(conv_id, hidden, forward_hidden)
        hidden = host.model.model.norm(hidden)
        logits = host._project_logits(hidden[:, -1:, :])
        prefill_ms = (time.perf_counter() - t0) * 1000
        host._token_counts[conv_id] = already_seen + len(new_ids)
        return PrefillOutput(logits[0, -1], len(new_ids), prefill_ms)

    async def decode_one(
        self,
        conv_id: str,
        token: int,
        *,
        forward_hidden: ForwardHidden | None = None,
    ):
        host = self._require_host()
        hidden = host.model.model.embed_tokens(array([[token]]))
        hidden = await self._forward_hidden(conv_id, hidden, forward_hidden)
        hidden = host.model.model.norm(hidden)
        logits = host._project_logits(hidden[:, -1:, :])
        host._token_counts[conv_id] = host._token_counts.get(conv_id, 0) + 1
        return logits[0, -1]

    @property
    def supports_speculation(self) -> bool:
        return True

    async def decode_many(
        self,
        conv_id: str,
        tokens: list[int],
        *,
        forward_hidden: ForwardHidden | None = None,
    ):
        """One pass over `tokens`: through every layer here and down the chain, once for all of them.

        Over a pool this costs one round trip per hop whatever the number of tokens, which is what makes
        verifying a draft cheaper than decoding its tokens one by one.
        """
        host = self._require_host()
        hidden = host.model.model.embed_tokens(array([tokens]))
        hidden = await self._forward_hidden(conv_id, hidden, forward_hidden)
        hidden = host.model.model.norm(hidden)
        logits = host._project_logits(hidden)
        host._token_counts[conv_id] = host._token_counts.get(conv_id, 0) + len(tokens)
        return logits[0]

    def trim(self, conv_id: str, n: int) -> None:
        self._require_host().trim(conv_id, n)

    def forward_segment(self, conv_id: str, hidden, start: int, end: int):
        return self._require_host().forward_segment(conv_id, hidden, start, end)

    def project_logits(self, hidden_last):
        return self._require_host()._project_logits(hidden_last)

    def decode_token(self, token: int) -> str:
        return self._require_host().tokenizer.decode([token])

    def decode_tokens(self, tokens: list[int]) -> str:
        return self._require_host().tokenizer.decode(tokens)

    def sample_token(
        self,
        logits_row,
        temperature: float,
        recent_tokens: list[int] | None = None,
        repetition_penalty: float = 1.0,
    ) -> int:
        return _sample(logits_row, temperature, recent_tokens, repetition_penalty)

    def encode_hidden(self, hidden) -> dict:
        from .wire import encode_mlx_array

        return encode_mlx_array(hidden)

    def decode_hidden(self, payload: dict):
        from .wire import decode_mlx_array

        return decode_mlx_array(payload)

    def stop_ids(self) -> set[int]:
        return _stop_ids(self._require_host().tokenizer)

    @property
    def active_params(self) -> int:
        return self._require_host().active_params

    @property
    def n_layers(self) -> int:
        return self._require_host().n_layers

    @property
    def geometry(self) -> ModelGeometry | None:
        return self._geometry

    def new_detokenizer(self) -> StreamDetokenizer:
        # `tokenizer.detokenizer` is a property that returns a *fresh*
        # StreamingDetokenizer each access, so this is concurrency-safe:
        # one independent detokenizer per generation stream.
        return _MlxStreamDetokenizer(self._require_host().tokenizer.detokenizer)

    async def unload(self) -> None:
        """Drop the loaded model + all KV caches and return the Metal
        buffer pool to the OS so the worker's RAM frees up."""
        import gc

        import mlx.core as mx

        host = self.host
        self.host = None
        self._geometry = None
        if host is not None:
            host._caches.clear()
            host._token_counts.clear()
            host.model = None
            host.tokenizer = None
        gc.collect()
        # Release cached Metal buffers held by MLX's allocator; without
        # this the freed weights stay in the buffer pool and the OS never
        # sees the RAM come back.
        mx.clear_cache()


class _MlxStreamDetokenizer(StreamDetokenizer):
    """Wraps mlx_lm's StreamingDetokenizer. It buffers incomplete
    multi-byte UTF-8 sequences and only surfaces complete characters via
    `last_segment`, so emoji/CJK that span token boundaries no longer
    decode to replacement characters (``�``)."""

    def __init__(self, detok):
        self._d = detok

    def add_token(self, token: int) -> str:
        self._d.add_token(token)
        return self._d.last_segment

    def finalize(self) -> str:
        self._d.finalize()
        return self._d.last_segment


__all__ = [
    "FullModelHost",
    "MlxBackendRuntime",
    "_sample",
    "_stop_ids",
    "create_attention_mask",
    "make_prompt_cache",
    "trim_prompt_cache",
]

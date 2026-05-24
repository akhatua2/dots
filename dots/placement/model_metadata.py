"""Model metadata helpers for runtime placement.

Runtime placement uses model metadata as data, not hardcoded model names.
Config supplies shape; safetensors metadata supplies exact checkpoint byte
counts for layer/head placement.
"""

from __future__ import annotations

import glob
import json
import logging
import struct
from pathlib import Path
from typing import Any, Protocol

from .work_units import (
    BYTES_PER_GB,
    DEFAULT_FLOAT16_BYTES_PER_PARAM,
    DEFAULT_QUANTIZED_BYTES_PER_PARAM,
    ModelGeometry,
)

log = logging.getLogger("dots.placement.model_metadata")


class LayerAssignment(Protocol):
    layer_start: int
    layer_end: int | None
    needs_head: bool


def _cfg_get(config: dict[str, Any], key: str) -> Any:
    if key in config:
        return config[key]
    text_config = config.get("text_config")
    if isinstance(text_config, dict):
        return text_config.get(key)
    return None


def _quantization_config(config: dict[str, Any]) -> Any:
    return (
        config.get("quantization")
        or config.get("quantization_config")
        or (
            config.get("text_config", {}).get("quantization")
            if isinstance(config.get("text_config"), dict)
            else None
        )
        or (
            config.get("text_config", {}).get("quantization_config")
            if isinstance(config.get("text_config"), dict)
            else None
        )
    )


def geometry_from_config(config: dict[str, Any]) -> ModelGeometry | None:
    n_layers = int(_cfg_get(config, "num_hidden_layers") or 0)
    hidden = int(_cfg_get(config, "hidden_size") or 0)
    vocab = int(_cfg_get(config, "vocab_size") or 0)
    if not (n_layers and hidden and vocab):
        return None
    quantization = _quantization_config(config)
    return ModelGeometry(
        n_layers=n_layers,
        hidden_size=hidden,
        vocab_size=vocab,
        layer_bytes_per_param=(
            DEFAULT_QUANTIZED_BYTES_PER_PARAM
            if quantization
            else DEFAULT_FLOAT16_BYTES_PER_PARAM
        ),
        head_bytes_per_param=DEFAULT_FLOAT16_BYTES_PER_PARAM,
        tie_word_embeddings=bool(_cfg_get(config, "tie_word_embeddings")),
        metadata_source="config",
    )


class ModelMetadataError(RuntimeError):
    """Raised when exact runtime placement metadata is unavailable."""


def safetensors_disk_size_gb(model_id: str | Path) -> float:
    """Exact safetensors file footprint for a local path or HF repo."""
    path = Path(model_id)
    if path.is_dir():
        files = sorted(path.glob("*.safetensors"))
        if not files:
            raise ModelMetadataError(f"no safetensors files found in {path}")
        return sum(f.stat().st_size for f in files) / BYTES_PER_GB

    try:
        from huggingface_hub import HfApi

        info = HfApi().repo_info(str(model_id), files_metadata=True)
    except Exception as e:
        raise ModelMetadataError(
            f"could not read repo file sizes for {model_id!r}: {e}"
        ) from e

    total = 0
    saw_weight = False
    missing_sizes: list[str] = []
    for sibling in info.siblings:
        name = getattr(sibling, "rfilename", "")
        if not name.endswith(".safetensors"):
            continue
        saw_weight = True
        size = getattr(sibling, "size", None)
        if size is None:
            missing_sizes.append(name)
            continue
        total += int(size)
    if missing_sizes:
        raise ModelMetadataError(
            "repo file metadata is missing sizes for "
            + ", ".join(sorted(missing_sizes))
        )
    if not saw_weight:
        raise ModelMetadataError(f"repo {model_id!r} has no safetensors files")
    return total / BYTES_PER_GB


def assignment_ram_gb(geometry: ModelGeometry, assignment: LayerAssignment) -> float:
    """Exact RAM footprint for an assigned layer slice and optional head."""
    if not geometry.layer_ram_gb or len(geometry.layer_ram_gb) != geometry.n_layers:
        raise ModelMetadataError("exact decoder layer RAM metadata is unavailable")
    layer_end = (
        assignment.layer_end if assignment.layer_end is not None else geometry.n_layers
    )
    if not (0 <= assignment.layer_start <= layer_end <= geometry.n_layers):
        raise ModelMetadataError(
            f"invalid layer assignment [{assignment.layer_start}:{layer_end}) "
            f"for {geometry.n_layers}-layer model"
        )
    ram_gb = sum(geometry.layer_ram_gb[assignment.layer_start : layer_end])
    if not assignment.needs_head:
        return ram_gb
    if geometry.embed_ram_gb is None:
        raise ModelMetadataError("exact embedding RAM metadata is unavailable")
    if geometry.lm_head_ram_gb is None and not geometry.tie_word_embeddings:
        raise ModelMetadataError("exact lm_head RAM metadata is unavailable")
    return (
        ram_gb
        + geometry.embed_ram_gb
        + (geometry.lm_head_ram_gb or 0.0)
        + geometry.head_extra_ram_gb
    )


def geometry_with_safetensors_metadata(
    model_id: str,
    base: ModelGeometry,
) -> ModelGeometry:
    """Attach exact safetensors byte counts from Hugging Face metadata.

    This uses metadata, not weight downloads. If exact byte offsets are
    unavailable, the caller gets a clear error instead of silent estimates.
    """
    try:
        from huggingface_hub import get_safetensors_metadata

        meta = get_safetensors_metadata(model_id)
    except Exception as e:
        raise ModelMetadataError(
            f"could not read safetensors metadata for {model_id!r}: {e}"
        ) from e

    return _geometry_from_tensor_sizes(
        base,
        _iter_hf_tensor_sizes(meta),
        metadata_source="safetensors",
    )


def fetch_model_geometry(model_id: str) -> ModelGeometry:
    """Backend-neutral model geometry, read straight from the HF `config.json`.

    This lets the hub plan placement WITHOUT a model runtime — crucially, the
    hub has no mlx/mlx_lm (it's Apple-only), so it can't go through the mlx
    backend to read geometry. We download just `config.json` via
    huggingface_hub, parse layers/hidden/vocab, and enrich with exact
    safetensors byte counts when the HF metadata exposes them (best-effort;
    config-only geometry is still enough for placement to proceed).
    """
    import json

    try:
        from huggingface_hub import hf_hub_download

        cfg_path = hf_hub_download(model_id, "config.json")
        with open(cfg_path) as f:
            config = json.load(f)
    except Exception as e:
        raise ModelMetadataError(
            f"could not fetch config.json for {model_id!r}: {e}"
        ) from e

    geom = geometry_from_config(config)
    if geom is None:
        raise ModelMetadataError(
            f"config.json for {model_id!r} is missing layers/hidden/vocab"
        )
    try:
        geom = geometry_with_safetensors_metadata(model_id, geom)
    except ModelMetadataError:
        pass  # exact per-tensor sizes unavailable; config-only geometry stands
    return geom


def geometry_with_local_safetensors_metadata(
    model_path: str | Path,
    base: ModelGeometry,
) -> ModelGeometry:
    """Attach exact byte counts from local safetensors headers."""
    path = Path(model_path)
    files = sorted(glob.glob(str(path / "*.safetensors")))
    if not files:
        raise ModelMetadataError(f"no safetensors files found in {path}")
    return _geometry_from_tensor_sizes(
        base,
        _iter_local_tensor_sizes(files),
        metadata_source="local_safetensors",
    )


def _iter_hf_tensor_sizes(meta):
    for file_meta in getattr(meta, "files_metadata", {}).values():
        tensors = getattr(file_meta, "tensors", {}) or {}
        for name, info in tensors.items():
            size_bytes = _tensor_size_bytes(info)
            if size_bytes is None:
                raise ModelMetadataError(
                    "safetensors metadata lacks tensor byte offsets"
                )
            yield name, size_bytes


def _iter_local_tensor_sizes(files: list[str]):
    header_len_struct = struct.Struct("<Q")
    for file_name in files:
        with open(file_name, "rb") as f:
            header_len_raw = f.read(header_len_struct.size)
            if len(header_len_raw) != header_len_struct.size:
                raise ModelMetadataError(f"{file_name} is not a valid safetensors file")
            (header_len,) = header_len_struct.unpack(header_len_raw)
            header = json.loads(f.read(header_len))
        for name, info in header.items():
            if name == "__metadata__":
                continue
            offsets = info.get("data_offsets")
            if not offsets or len(offsets) != 2:
                raise ModelMetadataError(f"{file_name}:{name} lacks byte offsets")
            yield name, int(offsets[1]) - int(offsets[0])


def _geometry_from_tensor_sizes(
    base: ModelGeometry,
    tensors,
    *,
    metadata_source: str,
) -> ModelGeometry:
    layer_bytes = [0] * base.n_layers
    embed_bytes = 0
    lm_head_bytes = 0
    head_extra_bytes = 0
    saw_tensor = False

    for name, size_bytes in tensors:
        saw_tensor = True
        layer_idx = _layer_index(name)
        if layer_idx is not None and 0 <= layer_idx < base.n_layers:
            layer_bytes[layer_idx] += size_bytes
        elif name.startswith("model.embed_tokens."):
            embed_bytes += size_bytes
        elif name.startswith("lm_head."):
            lm_head_bytes += size_bytes
        elif name.startswith("model.norm."):
            head_extra_bytes += size_bytes
        elif not name.startswith("model.layers."):
            # Small root-level tensors are needed by the head path in
            # current MLX partial loading. Count them instead of losing
            # bytes silently.
            head_extra_bytes += size_bytes

    if not saw_tensor:
        raise ModelMetadataError("safetensors metadata contains no tensors")
    if any(b <= 0 for b in layer_bytes):
        missing = [str(i) for i, b in enumerate(layer_bytes) if b <= 0]
        raise ModelMetadataError(
            "safetensors metadata is missing decoder layer bytes for layers "
            + ", ".join(missing)
        )
    if embed_bytes <= 0:
        raise ModelMetadataError(
            "safetensors metadata is missing model.embed_tokens bytes"
        )
    if lm_head_bytes <= 0 and not base.tie_word_embeddings:
        raise ModelMetadataError("safetensors metadata is missing lm_head bytes")

    return ModelGeometry(
        n_layers=base.n_layers,
        hidden_size=base.hidden_size,
        vocab_size=base.vocab_size,
        layer_bytes_per_param=base.layer_bytes_per_param,
        head_bytes_per_param=base.head_bytes_per_param,
        layer_ram_gb=tuple(b / BYTES_PER_GB for b in layer_bytes),
        embed_ram_gb=embed_bytes / BYTES_PER_GB if embed_bytes else None,
        lm_head_ram_gb=lm_head_bytes / BYTES_PER_GB if lm_head_bytes else None,
        head_extra_ram_gb=head_extra_bytes / BYTES_PER_GB,
        tie_word_embeddings=base.tie_word_embeddings,
        metadata_source=metadata_source,
    )


def _tensor_size_bytes(info: Any) -> int | None:
    offsets = getattr(info, "data_offsets", None)
    if offsets and len(offsets) == 2:
        return int(offsets[1]) - int(offsets[0])
    return None


def _layer_index(name: str) -> int | None:
    if not name.startswith("model.layers."):
        return None
    parts = name.split(".", 3)
    if len(parts) < 3:
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None

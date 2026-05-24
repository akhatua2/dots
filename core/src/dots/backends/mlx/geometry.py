"""MLX model geometry inspection."""

from __future__ import annotations

import logging
from typing import Any

from ...placement.work_units import (
    DEFAULT_FLOAT16_BYTES_PER_PARAM,
    DEFAULT_QUANTIZED_BYTES_PER_PARAM,
    ModelGeometry,
)

log = logging.getLogger("dots.backends.mlx.geometry")


def geometry_from_mlx_model(model: Any) -> ModelGeometry | None:
    """Inspect an mlx-lm-loaded model and return its geometry.

    Returns None if the object doesn't look like an MLX transformer. This
    is intentionally kept under the MLX backend package.
    """
    try:
        cfg = getattr(model, "config", None) or getattr(model, "args", None)
        if cfg is None:
            return None
        layers = getattr(model, "model", model)
        n_layers = len(getattr(layers, "layers", []))
        if n_layers == 0:
            return None
        hidden = (
            getattr(cfg, "hidden_size", None)
            or getattr(cfg, "model_dim", None)
            or getattr(cfg, "d_model", None)
        )
        vocab = getattr(cfg, "vocab_size", None) or getattr(cfg, "n_vocab", None)
        if hidden is None or vocab is None:
            return None
        quantization = getattr(cfg, "quantization", None) or getattr(
            cfg, "quantization_config", None
        )
        return ModelGeometry(
            n_layers=int(n_layers),
            hidden_size=int(hidden),
            vocab_size=int(vocab),
            layer_bytes_per_param=(
                DEFAULT_QUANTIZED_BYTES_PER_PARAM
                if quantization
                else DEFAULT_FLOAT16_BYTES_PER_PARAM
            ),
            head_bytes_per_param=DEFAULT_FLOAT16_BYTES_PER_PARAM,
            tie_word_embeddings=bool(getattr(cfg, "tie_word_embeddings", False)),
            metadata_source="mlx_model",
        )
    except Exception:
        log.debug("MLX model geometry inspection failed", exc_info=True)
        return None

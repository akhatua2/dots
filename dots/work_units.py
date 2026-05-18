"""Build a scheduler `WorkUnit` chain for a real loaded model.

The scheduler operates on abstract `WorkUnit`s (embed / decoder block /
norm / lm_head / sample). To plan placements for a query on a real model
that the hub has loaded with `mlx_lm.load`, we need to translate the
model's architecture into that chain.

This module is the only place that bridges from "live MLX model" to
"abstract scheduler unit DAG". Pure-Python; depends on `mlx` only for
optional shape inspection (falls back to estimates if the model object
isn't an MLX model — e.g. test fakes).

The function `units_for_model` returns a list of WorkUnit that the
scheduler can feed into `enumerate_placements`. Per-unit RAM is the
weight footprint; per-token FLOPs is the matmul work this unit does
each generation step.

Why is this here and not folded into `sim_pool.py`?

  • `sim_pool.py` produces synthetic test pools and idealised model
    specs by name. Tests use it without ever loading a model.
  • `work_units.py` operates on an actual loaded model object (or a
    minimal config dict). The hub calls this when it has the real model
    in hand and needs to plan a placement for an incoming query.

They share the same output type (list[WorkUnit]); they differ in inputs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .scheduler import WorkUnit

log = logging.getLogger("dots.work_units")


# ──────────────────────────── inspection ────────────────────────────


@dataclass(frozen=True)
class ModelGeometry:
    """The shape facts about a model we need for scheduling.

    Pulled out of `units_for_model` so we can also synthesize it for
    test fakes that don't expose a real `.config`.
    """
    n_layers: int
    hidden_size: int
    vocab_size: int
    # Bytes-per-parameter heuristic: 0.5 for 4-bit-quantized decoder layers,
    # 2 for bf16 embed/lm_head. These are *defaults*; an MoE model or a
    # bf16 checkpoint would override.
    layer_bytes_per_param: float = 0.5
    head_bytes_per_param: float = 2.0


def geometry_from_mlx_model(model: Any) -> ModelGeometry | None:
    """Inspect an mlx-lm-loaded model and return its geometry.

    Returns None if the object doesn't look like an MLX transformer (test
    fakes, custom models). Caller can fall back to `ModelGeometry(...)`
    constructed by hand.
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
        vocab = (
            getattr(cfg, "vocab_size", None)
            or getattr(cfg, "n_vocab", None)
        )
        if hidden is None or vocab is None:
            return None
        return ModelGeometry(
            n_layers=int(n_layers),
            hidden_size=int(hidden),
            vocab_size=int(vocab),
        )
    except Exception:
        log.debug("model geometry inspection failed", exc_info=True)
        return None


# ──────────────────────────── unit builder ──────────────────────────


# Per-layer parameter count is estimated as F · hidden² (attention QKV/O
# is ~4 H², the SwiGLU MLP at intermediate ≈ 4 H is ~12 H², plus norms).
# Matches Qwen-2.5-7B (~232 M / layer at H=3584) and Llama-3.1-70B
# (~880 M / layer at H=8192) within ~10%.
_PER_LAYER_PARAM_FACTOR = 16


def units_for_geometry(
    geom: ModelGeometry,
    layers_per_block: int = 1,
) -> list[WorkUnit]:
    """Build a per-token unit chain for a given geometry.

    `layers_per_block` lets the caller group N adjacent decoder layers
    into one schedulable unit. With `1`, every layer is its own unit
    (28-unit chain for Qwen-7B). For very deep models (70B has 80 layers)
    the scheduler's enumeration is exponential in unit count, so the hub
    can group with `layers_per_block=4` or `8` to keep planning fast.
    """
    head_params = geom.vocab_size * geom.hidden_size
    head_ram_gb = head_params * geom.head_bytes_per_param / 1e9

    per_layer_params = _PER_LAYER_PARAM_FACTOR * geom.hidden_size ** 2
    per_layer_ram_gb = per_layer_params * geom.layer_bytes_per_param / 1e9
    per_layer_flops = 2 * per_layer_params  # MAC = 2 FLOPs

    units: list[WorkUnit] = [
        WorkUnit("embed", ram_required_gb=head_ram_gb, flops_per_token=0),
    ]

    n_blocks, remainder = divmod(geom.n_layers, layers_per_block)
    block_sizes = [layers_per_block] * n_blocks
    if remainder:
        block_sizes.append(remainder)

    for i, count in enumerate(block_sizes):
        units.append(WorkUnit(
            f"block_{i}",
            ram_required_gb=per_layer_ram_gb * count,
            flops_per_token=per_layer_flops * count,
        ))

    units.append(WorkUnit("final_norm",
                          ram_required_gb=0.001,
                          flops_per_token=geom.hidden_size))
    units.append(WorkUnit("lm_head",
                          ram_required_gb=head_ram_gb,
                          flops_per_token=2 * head_params))
    units.append(WorkUnit("sample",
                          ram_required_gb=0.001,
                          flops_per_token=geom.vocab_size))
    return units


def units_for_model(
    model: Any,
    layers_per_block: int = 1,
    fallback_geometry: ModelGeometry | None = None,
) -> list[WorkUnit] | None:
    """Inspect the live model and return its scheduler unit chain.

    Returns None when the model object isn't introspectable *and* no
    `fallback_geometry` is supplied. Hub callers should provide a fallback
    when running with test fakes.
    """
    geom = geometry_from_mlx_model(model) or fallback_geometry
    if geom is None:
        return None
    return units_for_geometry(geom, layers_per_block=layers_per_block)

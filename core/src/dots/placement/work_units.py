"""Build scheduler `WorkUnit` chains from backend-neutral model geometry.

The scheduler operates on abstract `WorkUnit`s (embed / decoder block /
norm / lm_head / sample). It consumes `ModelGeometry` provided by model
metadata or a backend runtime; it does not inspect backend-specific model
objects.

The function `units_for_geometry` returns a list of WorkUnit that the
scheduler can feed into `enumerate_placements`. Per-unit RAM is the
weight footprint; per-token FLOPs is the matmul work this unit does
each generation step.

Why is this here and not folded into `sim_pool.py`?

  • `sim_pool.py` produces synthetic test pools and idealised model
    specs by name. Tests use it without ever loading a model.
  • `work_units.py` operates on backend-neutral geometry. Backends that
    can inspect live models should translate them to `ModelGeometry`
    inside their own package.

They share the same output type (list[WorkUnit]); they differ in inputs.
"""

from __future__ import annotations

from dataclasses import dataclass

from .schema import ModelComponent
from .scheduler import WorkUnit

BYTES_PER_GB = 1_000_000_000
DEFAULT_QUANTIZED_BYTES_PER_PARAM = 0.5
DEFAULT_FLOAT16_BYTES_PER_PARAM = 2.0
TRANSFORMER_LAYER_PARAM_FACTOR = 16
FLOPS_PER_MAC = 2
MIN_SCALAR_UNIT_RAM_GB = 0.001


@dataclass(frozen=True)
class ModelGeometry:
    """The shape facts about a model we need for scheduling.

    Produced by model metadata readers or backend-specific inspection.
    """

    n_layers: int
    hidden_size: int
    vocab_size: int
    # Bytes-per-parameter values from model config. Runtime placement
    # should prefer exact `*_ram_gb` metadata when available.
    layer_bytes_per_param: float = DEFAULT_QUANTIZED_BYTES_PER_PARAM
    head_bytes_per_param: float = DEFAULT_FLOAT16_BYTES_PER_PARAM
    # Exact RAM facts from backend/model metadata.
    layer_ram_gb: tuple[float, ...] | None = None
    embed_ram_gb: float | None = None
    lm_head_ram_gb: float | None = None
    head_extra_ram_gb: float = 0.0
    tie_word_embeddings: bool = False
    metadata_source: str = "config"
    components: tuple[ModelComponent, ...] = ()


# ──────────────────────────── unit builder ──────────────────────────


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
    if geom.components:
        return [
            WorkUnit(
                component.component_id,
                ram_required_gb=component.ram_gb,
                flops_per_token=component.flops_per_token,
            )
            for component in sorted(geom.components, key=lambda c: c.order)
        ]

    head_params = geom.vocab_size * geom.hidden_size
    default_head_ram_gb = head_params * geom.head_bytes_per_param / BYTES_PER_GB
    embed_ram_gb = (
        geom.embed_ram_gb if geom.embed_ram_gb is not None else default_head_ram_gb
    )
    if geom.lm_head_ram_gb is not None:
        lm_head_ram_gb = geom.lm_head_ram_gb
    else:
        lm_head_ram_gb = 0.0 if geom.tie_word_embeddings else default_head_ram_gb

    per_layer_params = TRANSFORMER_LAYER_PARAM_FACTOR * geom.hidden_size**2
    per_layer_ram_gb = per_layer_params * geom.layer_bytes_per_param / BYTES_PER_GB
    per_layer_flops = FLOPS_PER_MAC * per_layer_params

    units: list[WorkUnit] = [
        WorkUnit("embed", ram_required_gb=embed_ram_gb, flops_per_token=0),
    ]

    n_blocks, remainder = divmod(geom.n_layers, layers_per_block)
    block_sizes = [layers_per_block] * n_blocks
    if remainder:
        block_sizes.append(remainder)

    cursor = 0
    for i, count in enumerate(block_sizes):
        if geom.layer_ram_gb and len(geom.layer_ram_gb) == geom.n_layers:
            ram_required_gb = sum(geom.layer_ram_gb[cursor : cursor + count])
        else:
            ram_required_gb = per_layer_ram_gb * count
        units.append(
            WorkUnit(
                f"block_{i}",
                ram_required_gb=ram_required_gb,
                flops_per_token=per_layer_flops * count,
            )
        )
        cursor += count

    units.append(
        WorkUnit(
            "final_norm",
            ram_required_gb=max(
                MIN_SCALAR_UNIT_RAM_GB,
                geom.head_extra_ram_gb,
            ),
            flops_per_token=geom.hidden_size,
        )
    )
    units.append(
        WorkUnit(
            "lm_head",
            ram_required_gb=lm_head_ram_gb,
            flops_per_token=FLOPS_PER_MAC * head_params,
        )
    )
    units.append(
        WorkUnit(
            "sample",
            ram_required_gb=MIN_SCALAR_UNIT_RAM_GB,
            flops_per_token=geom.vocab_size,
        )
    )
    return units

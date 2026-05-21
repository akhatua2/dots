"""Known synthetic model specs + a per-token unit-graph builder."""

from __future__ import annotations

from dataclasses import dataclass

from dots.placement.scheduler import WorkUnit


@dataclass(frozen=True)
class ModelSpec:
    """Minimal architectural facts needed to build a unit graph.

    Per-layer params are approximated as F · hidden² (factor 16 covers
    SwiGLU MLP at intermediate ≈ 4 H plus attention QKV/O). Matches
    Qwen-2.5-7B and Llama-3.1-70B within 10%.
    """

    name: str
    n_layers: int
    hidden_size: int
    vocab_size: int
    layer_bytes_per_param: float = 0.5
    head_bytes_per_param: float = 2.0


QWEN_05B = ModelSpec(
    "Qwen-2.5-0.5B-Instruct-4bit", n_layers=24, hidden_size=896, vocab_size=151_936
)
QWEN_7B = ModelSpec(
    "Qwen-2.5-7B-Instruct-4bit", n_layers=28, hidden_size=3584, vocab_size=152_064
)
LLAMA_70B = ModelSpec(
    "Llama-3.1-70B-Instruct-4bit", n_layers=80, hidden_size=8192, vocab_size=128_256
)


def make_unit_graph(model: ModelSpec, layers_per_block: int = 1) -> list[WorkUnit]:
    """Build a per-token work-unit chain for `model`.

    Chain: embed → block_0 → ... → block_{B-1} → final_norm → lm_head → sample.
    Each block holds `layers_per_block` decoder layers; the remainder
    (if any) goes into a final trailing block.

    Assumptions:
      * Decoder layers default to 4-bit weights (0.5 B/param).
      * Embedding + lm_head default to bf16 (2 B/param), unquantized.
    """
    head_params = model.vocab_size * model.hidden_size
    head_ram_gb = head_params * model.head_bytes_per_param / 1e9
    per_layer_params = 16 * model.hidden_size * model.hidden_size
    per_layer_ram_gb = per_layer_params * model.layer_bytes_per_param / 1e9
    per_layer_flops = 2 * per_layer_params

    units: list[WorkUnit] = [
        WorkUnit("embed", ram_required_gb=head_ram_gb, flops_per_token=0),
    ]

    n_blocks, remainder = divmod(model.n_layers, layers_per_block)
    block_sizes = [layers_per_block] * n_blocks
    if remainder:
        block_sizes.append(remainder)

    for i, count in enumerate(block_sizes):
        units.append(
            WorkUnit(
                f"block_{i}",
                ram_required_gb=per_layer_ram_gb * count,
                flops_per_token=per_layer_flops * count,
            )
        )

    units.append(
        WorkUnit("final_norm", ram_required_gb=0.001, flops_per_token=model.hidden_size)
    )
    units.append(
        WorkUnit(
            "lm_head", ram_required_gb=head_ram_gb, flops_per_token=2 * head_params
        )
    )
    units.append(
        WorkUnit("sample", ram_required_gb=0.001, flops_per_token=model.vocab_size)
    )
    return units

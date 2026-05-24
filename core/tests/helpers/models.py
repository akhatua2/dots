"""Model metadata builders for tests."""

from __future__ import annotations

from dots.placement.work_units import ModelGeometry


def exact_geometry(
    *,
    n_layers: int = 4,
    hidden_size: int = 128,
    vocab_size: int = 1000,
    layer_ram_gb: float = 0.001,
    embed_ram_gb: float = 0.001,
    lm_head_ram_gb: float | None = None,
    tie_word_embeddings: bool = True,
) -> ModelGeometry:
    """Build exact model geometry so placement tests avoid heuristics."""
    return ModelGeometry(
        n_layers=n_layers,
        hidden_size=hidden_size,
        vocab_size=vocab_size,
        layer_ram_gb=tuple(layer_ram_gb for _ in range(n_layers)),
        embed_ram_gb=embed_ram_gb,
        lm_head_ram_gb=lm_head_ram_gb,
        tie_word_embeddings=tie_word_embeddings,
        metadata_source="test",
    )

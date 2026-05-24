"""Unit tests for `dots.work_units`.

These tests don't need a real MLX model — they exercise the geometry →
unit-chain builder directly. Hub integration tests (with an actual model
loaded) live in test_e2e or similar.
"""

from __future__ import annotations

from dots.placement.work_units import (
    ModelGeometry,
    units_for_geometry,
)


def test_unit_chain_starts_with_embed_ends_with_sample():
    geom = ModelGeometry(n_layers=4, hidden_size=512, vocab_size=10_000)
    units = units_for_geometry(geom)
    assert units[0].unit_id == "embed"
    assert units[-1].unit_id == "sample"
    assert units[-2].unit_id == "lm_head"
    assert units[-3].unit_id == "final_norm"


def test_unit_chain_block_count_matches_n_layers():
    geom = ModelGeometry(n_layers=28, hidden_size=3584, vocab_size=152_064)
    units = units_for_geometry(geom)
    block_units = [u for u in units if u.unit_id.startswith("block_")]
    assert len(block_units) == 28


def test_layers_per_block_groups_correctly():
    geom = ModelGeometry(n_layers=28, hidden_size=3584, vocab_size=152_064)
    units = units_for_geometry(geom, layers_per_block=4)
    block_units = [u for u in units if u.unit_id.startswith("block_")]
    assert len(block_units) == 7  # 28 / 4


def test_layers_per_block_handles_remainder():
    """80 layers grouped by 6 → 13 full blocks + 1 with 2 layers."""
    geom = ModelGeometry(n_layers=80, hidden_size=8192, vocab_size=128_256)
    units = units_for_geometry(geom, layers_per_block=6)
    block_units = [u for u in units if u.unit_id.startswith("block_")]
    assert len(block_units) == 14
    # Last block has remainder 80 - 6*13 = 2 layers worth of RAM
    expected_full = block_units[0].ram_required_gb
    assert abs(block_units[-1].ram_required_gb - expected_full / 3) < 1e-6


def test_embed_and_lm_head_have_matching_ram():
    """Embed and lm_head are both vocab × hidden bf16 — same footprint."""
    geom = ModelGeometry(n_layers=4, hidden_size=512, vocab_size=10_000)
    units = units_for_geometry(geom)
    embed = next(u for u in units if u.unit_id == "embed")
    lm_head = next(u for u in units if u.unit_id == "lm_head")
    assert abs(embed.ram_required_gb - lm_head.ram_required_gb) < 1e-9


def test_embed_has_zero_flops():
    """Embed is a row lookup — we charge 0 FLOPs."""
    geom = ModelGeometry(n_layers=4, hidden_size=512, vocab_size=10_000)
    units = units_for_geometry(geom)
    embed = next(u for u in units if u.unit_id == "embed")
    assert embed.flops_per_token == 0

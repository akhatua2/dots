"""Unit tests for `dots.sharding`."""
from __future__ import annotations

import pytest

from dots.sharding import (
    ExpertParallel,
    PipelineParallel,
    TensorParallel,
    available_strategies,
    pick_strategy,
    strategy_by_name,
)
from dots.work_units import ModelGeometry


# ─────────────────── registry shape ───────────────────


def test_registry_has_all_three():
    names = {s.profile.name for s in available_strategies()}
    assert names == {"pp", "tp", "ep"}


def test_strategy_by_name():
    assert isinstance(strategy_by_name("pp"), PipelineParallel)
    assert isinstance(strategy_by_name("tp"), TensorParallel)
    assert isinstance(strategy_by_name("ep"), ExpertParallel)


def test_unknown_strategy_raises():
    with pytest.raises(KeyError):
        strategy_by_name("unknown")


# ─────────────────── profile semantics ───────────────────


def test_pp_does_not_need_high_bandwidth():
    assert PipelineParallel().profile.needs_high_bandwidth is False
    assert PipelineParallel().profile.works_across_clusters is True


def test_tp_needs_high_bandwidth():
    assert TensorParallel().profile.needs_high_bandwidth is True
    assert TensorParallel().profile.works_across_clusters is False


def test_ep_needs_moe_model():
    assert ExpertParallel().profile.needs_moe_model is True


# ─────────────────── PP: actual planning works ───────────────────


def test_pp_builds_unit_graph():
    geom = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    units = PipelineParallel().build_unit_graph(geom)
    assert units[0].unit_id == "embed"
    assert units[-1].unit_id == "sample"


# ─────────────────── TP/EP are scaffolded ───────────────────


def test_tp_build_unit_graph_raises():
    geom = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    with pytest.raises(NotImplementedError):
        TensorParallel().build_unit_graph(geom)


def test_ep_build_unit_graph_raises():
    geom = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    with pytest.raises(NotImplementedError):
        ExpertParallel().build_unit_graph(geom)


# ─────────────────── strategy picker ───────────────────


def test_pick_strategy_default_pp():
    s = pick_strategy(is_moe=False, has_high_bandwidth_tier=False)
    assert s.profile.name == "pp"


def test_pick_strategy_moe_falls_back_to_pp_until_ep_implemented():
    """Today ExpertParallel isn't implemented — picker must fall back to PP
    rather than handing the application an unusable strategy."""
    s = pick_strategy(is_moe=True, has_high_bandwidth_tier=False)
    assert s.profile.name == "pp"


def test_pick_strategy_explicit_pp():
    s = pick_strategy(is_moe=False, has_high_bandwidth_tier=False, explicit="pp")
    assert isinstance(s, PipelineParallel)


def test_pick_strategy_explicit_unknown_raises():
    with pytest.raises(KeyError):
        pick_strategy(is_moe=False, has_high_bandwidth_tier=False, explicit="xx")

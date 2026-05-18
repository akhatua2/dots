"""Unit tests for `dots.compute_plane`.

LocalComputePlane needs a real-ish model (we won't actually run inference
here — that's covered elsewhere), so these tests stub the heavy parts
out. DistributedComputePlane's planning logic is fully testable in pure
Python without spinning up any model.
"""
from __future__ import annotations

import pytest

from dots.compute_plane import (
    DistributedComputePlane,
    create_plane,
)
from dots.scheduler import LatencyMatrix, ResourceDecl
from dots.work_units import ModelGeometry


def _decl(wid: str, ram: float = 16.0, tflops: float = 1.0) -> ResourceDecl:
    return ResourceDecl(
        worker_id=wid,
        account_id=f"acc-{wid}",
        ram_gb_free=ram,
        compute_tflops_per_sec=tflops,
        backends=frozenset({"mlx"}),
        cluster_id="A",
    )


# ────────────────── DistributedComputePlane.placement ──────────────────


def test_distributed_plane_plans_placement():
    """Plane construction picks a placement using the scheduler under
    its strategy. Small synthetic geometry → fits on one worker."""
    geom = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    workers = [_decl("W1", ram=8.0), _decl("W2", ram=16.0)]
    lat = LatencyMatrix({("W1", "W2"): 1.0})
    plane = DistributedComputePlane(
        geometry=geom, workers=workers, latency=lat,
        querier_account="acc-W1", mode="cheap",
    )
    assert plane.placement is not None
    # Self-utilization: should land on W1 (the querier).
    assert all(w == "W1" for w, _ in plane.placement.segments)


def test_distributed_plane_uses_pp_by_default():
    geom = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    workers = [_decl("W1"), _decl("W2")]
    lat = LatencyMatrix({("W1", "W2"): 5.0})
    plane = DistributedComputePlane(
        geometry=geom, workers=workers, latency=lat,
    )
    assert plane.strategy.profile.name == "pp"


def test_distributed_plane_routing_table_owns_every_unit():
    """Every unit in the graph must be routed to exactly one worker."""
    geom = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    workers = [_decl("W1", ram=8.0), _decl("W2", ram=8.0)]
    lat = LatencyMatrix({("W1", "W2"): 1.0})
    plane = DistributedComputePlane(
        geometry=geom, workers=workers, latency=lat,
    )
    for unit in plane._units:
        owner = plane.owner_of_unit(unit.unit_id)
        assert owner is not None
        assert owner.worker_id in {"W1", "W2"}


def test_distributed_plane_virtual_resources_sums_correctly():
    geom = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    workers = [_decl("W1", ram=8.0, tflops=0.5),
               _decl("W2", ram=16.0, tflops=1.5)]
    lat = LatencyMatrix({("W1", "W2"): 1.0})
    plane = DistributedComputePlane(
        geometry=geom, workers=workers, latency=lat,
    )
    vr = plane.virtual_resources
    assert vr.ram_gb_available == 24.0
    assert vr.compute_tflops_per_sec == 2.0
    assert vr.n_workers == 2
    assert vr.backends == frozenset({"mlx"})


def test_distributed_plane_raises_when_no_workers():
    geom = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    with pytest.raises(ValueError):
        DistributedComputePlane(geometry=geom, workers=[], latency=LatencyMatrix())


def test_distributed_plane_raises_when_model_too_big():
    """No feasible placement → constructor raises with a clear message."""
    geom = ModelGeometry(n_layers=80, hidden_size=8192, vocab_size=128_256)
    workers = [_decl("tiny", ram=0.1)]
    lat = LatencyMatrix()
    with pytest.raises(RuntimeError):
        DistributedComputePlane(geometry=geom, workers=workers, latency=lat)


def test_distributed_plane_explicit_strategy_pp():
    geom = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    workers = [_decl("W1"), _decl("W2")]
    lat = LatencyMatrix({("W1", "W2"): 1.0})
    plane = DistributedComputePlane(
        geometry=geom, workers=workers, latency=lat,
        explicit_strategy="pp",
    )
    assert plane.strategy.profile.name == "pp"


def test_distributed_plane_generate_is_scaffolded():
    """Until the hybrid-pipeline worker-side handler ships, generate raises."""
    geom = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    workers = [_decl("W1"), _decl("W2")]
    lat = LatencyMatrix({("W1", "W2"): 1.0})
    plane = DistributedComputePlane(
        geometry=geom, workers=workers, latency=lat,
    )
    import asyncio
    with pytest.raises(NotImplementedError):
        asyncio.get_event_loop().run_until_complete(
            plane.generate(history=[{"role": "user", "content": "hi"}])
        )


# ────────────────── create_plane factory ──────────────────


def test_create_plane_no_workers_returns_local(monkeypatch):
    """Without workers we get a LocalComputePlane. We don't actually
    construct it (mlx-lm load is heavy) — just verify the dispatch logic."""
    from dots.compute_plane import factory as cp_factory

    class _StubLocal:
        def __init__(self, model_id):
            self.model_id = model_id

    monkeypatch.setattr(cp_factory, "LocalComputePlane", _StubLocal)
    plane = cp_factory.create_plane(model_id="fake/x")
    assert isinstance(plane, _StubLocal)
    assert plane.model_id == "fake/x"


def test_create_plane_with_pool_returns_distributed(monkeypatch):
    from dots.compute_plane import factory as cp_factory

    class _StubLocal:
        def __init__(self, model_id):
            self.model_id = model_id

    monkeypatch.setattr(cp_factory, "LocalComputePlane", _StubLocal)
    geom = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    workers = [_decl("W1"), _decl("W2")]
    lat = LatencyMatrix({("W1", "W2"): 1.0})
    plane = cp_factory.create_plane(
        model_id="fake/x",
        geometry=geom,
        workers=workers,
        latency=lat,
    )
    assert isinstance(plane, DistributedComputePlane)


def test_create_plane_falls_back_to_local_when_infeasible(monkeypatch):
    """Pool can't fit the model → graceful fall-back to local, not crash."""
    from dots.compute_plane import factory as cp_factory

    class _StubLocal:
        def __init__(self, model_id):
            self.model_id = model_id

    monkeypatch.setattr(cp_factory, "LocalComputePlane", _StubLocal)
    geom = ModelGeometry(n_layers=80, hidden_size=8192, vocab_size=128_256)
    workers = [_decl("tiny", ram=0.1)]
    lat = LatencyMatrix()
    plane = cp_factory.create_plane(
        model_id="fake/x",
        geometry=geom,
        workers=workers,
        latency=lat,
    )
    assert isinstance(plane, _StubLocal)


def test_create_plane_force_local(monkeypatch):
    from dots.compute_plane import factory as cp_factory

    class _StubLocal:
        def __init__(self, model_id):
            self.model_id = model_id

    monkeypatch.setattr(cp_factory, "LocalComputePlane", _StubLocal)
    geom = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    workers = [_decl("W1")]
    lat = LatencyMatrix()
    plane = cp_factory.create_plane(
        model_id="fake/x", geometry=geom, workers=workers,
        latency=lat, force_local=True,
    )
    assert isinstance(plane, _StubLocal)

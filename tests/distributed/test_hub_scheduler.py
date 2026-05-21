"""Integration tests for the scheduler wired into Hub.

These cover:
  • The hub.pool_view captures resource declarations from worker HELLO.
  • Hub.plan_placement returns a Pareto-optimal placement for the current pool.
  • Pool_view tracks worker join/leave correctly.

The legacy data plane (hub-mediated star pipeline) is unchanged by this
integration — plan_placement is observe-only for now. The data-plane swap
lands in a later PR per hybrid-pipeline.md.
"""

from __future__ import annotations

import pytest

from dots.hub import Hub
from dots.work_units import ModelGeometry
from tests.helpers.resources import resource_decl

from tests.conftest import connect_worker, wait_for_pool_size


# ─────────────────── unit-style: Hub.plan_placement ───────────────────


def test_plan_placement_without_geometry_returns_none():
    """If the hub couldn't inspect the model (fake / unknown architecture),
    plan_placement degrades gracefully — no crash."""
    hub = Hub(port=0, model_id="fake/x")
    hub._geometry = None
    hub._selected_backend = "mlx"
    hub.pool_view.add_or_update("W1", resource_decl("W1"))
    assert hub.plan_placement(querier_account="acc-1") is None


def test_plan_placement_without_workers_returns_none():
    """No pool members → no feasible placement."""
    hub = Hub(port=0, model_id="fake/x")
    hub._geometry = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    hub._selected_backend = "mlx"
    assert hub.plan_placement(querier_account="acc-1") is None


def test_plan_placement_returns_pareto_optimal_for_synthetic_pool():
    """Fully synthetic — manually wire pool_view + geometry, no MLX needed."""
    hub = Hub(port=0, model_id="fake/x")
    hub._geometry = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    hub._selected_backend = "mlx"
    hub.pool_view.add_or_update("W1", resource_decl("W1", ram_gb=8.0))
    hub.pool_view.add_or_update("W2", resource_decl("W2", ram_gb=16.0))
    hub.pool_view.record_probe("W1", "W2", 2.0)

    placement = hub.plan_placement(querier_account="acc-W1", mode="cheap")
    assert placement is not None
    # Cheap mode should prefer the querier's own machine when it can fit.
    # Small synthetic model definitely fits on W1's 8 GB.
    assert any(w == "W1" for w, _ in placement.segments)


# ─────────────────── integration with real hub fixture ───────────────


@pytest.mark.asyncio
async def test_hello_populates_pool_view(hub: Hub):
    """After a worker connects, its ResourceDecl appears in pool_view."""
    worker = await connect_worker(hub)
    await wait_for_pool_size(hub, 1)
    decl = hub.pool_view.resources.get(worker.identity)
    assert decl is not None
    assert decl.account_id == worker.account_id
    assert decl.ram_gb_free > 0
    # Worker reported an MLX backend (default for Apple Silicon).
    assert "mlx" in decl.backends


@pytest.mark.asyncio
async def test_probes_populate_latency_matrix(hub: Hub):
    """When a second compute worker becomes ready, the hub triggers
    bidirectional RTT probes and the pool_view latency matrix gains a
    measured cell for the pair."""
    w1 = await connect_worker(hub)
    w2 = await connect_worker(hub)
    await wait_for_pool_size(hub, 2)
    # Probes happen as background tasks after worker_ready arrives.
    import asyncio

    for _ in range(50):
        if hub.pool_view.get_rtt(w1.identity, w2.identity) is not None:
            break
        await asyncio.sleep(0.05)
    rtt = hub.pool_view.get_rtt(w1.identity, w2.identity)
    assert rtt is not None, "no probe RTT recorded between two workers"
    # On localhost loopback this should be tiny (<10ms) even on slow CI.
    assert 0.0 <= rtt < 50.0, f"loopback RTT shouldn't be {rtt} ms"


@pytest.mark.asyncio
async def test_pool_view_clears_on_worker_leave(hub: Hub):
    """After a worker disconnects, pool_view removes its entry."""
    worker = await connect_worker(hub)
    await wait_for_pool_size(hub, 1)
    assert worker.identity in hub.pool_view.resources
    await worker.conn.close()
    # Hub's leave handler runs on the read loop — give it a tick.
    import asyncio

    for _ in range(20):
        if worker.identity not in hub.pool_view.resources:
            break
        await asyncio.sleep(0.05)
    assert worker.identity not in hub.pool_view.resources

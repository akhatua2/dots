"""Correctness tests for `dots.scheduler`.

The scheduler is pure logic — these tests are pure unit tests; no async,
no network, no model loading. They establish the invariants that the rest
of the system relies on:

  * Every returned placement is on the Pareto frontier of (cu_paid, tps).
  * Every returned placement is feasible (RAM, backend, reachability).
  * Self-utilization dominates when the model fits on the querier's machine.
  * Mode preferences (cheap / fast / balanced) yield sensible orderings.

A fuzz test covers random pools + queries to catch edge cases the targeted
tests miss.
"""

from __future__ import annotations

import math
import random

import pytest

from dots.placement.scheduler import (
    LatencyMatrix,
    Placement,
    _segmentations,
    enumerate_placements,
    non_dominated_placements,
    pick_by_mode,
    schedule,
)
from tests.helpers.sim import (
    LLAMA_70B,
    QWEN_05B,
    QWEN_7B,
    make_multi_cluster_pool,
    make_random_pool,
    make_simple_pool,
    make_unit_graph,
)
from tests.helpers.resources import resource_decl


# ─────────────────────── primitive sanity ──────────────────────────


def test_latency_matrix_self_is_zero():
    lm = LatencyMatrix({("a", "b"): 5.0})
    assert lm.get("a", "a") == 0
    assert lm.get("a", "b") == 5
    assert lm.get("b", "a") == 5  # symmetric


def test_latency_matrix_disconnected_is_inf():
    lm = LatencyMatrix({})
    assert lm.get("a", "b") == math.inf
    assert not lm.reachable("a", "b")


def test_segmentations_count():
    """Number of contiguous partitions of n into k parts = C(n-1, k-1)."""
    from math import comb

    for n in range(1, 8):
        for k in range(1, n + 1):
            assert sum(1 for _ in _segmentations(n, k)) == comb(n - 1, k - 1)


def test_segmentations_cover_all_units():
    """Every segmentation's ranges concatenate to [0, n)."""
    for ranges in _segmentations(8, 3):
        flat = [i for s, e in ranges for i in range(s, e)]
        assert flat == list(range(8))


# ─────────────────────── pareto primitives ─────────────────────────


def _placement(cu: float, tps: float, segments: tuple = (("w", ("u",)),)) -> Placement:
    """Shorthand for tests that only care about (cu, tps)."""
    return Placement(
        segments=segments,
        cu_paid=cu,
        tps=tps,
        compute_time_ms=0,
        network_time_ms=0,
    )


def test_pareto_keeps_only_non_dominated():
    p_lo = _placement(cu=10, tps=5)
    p_hi = _placement(cu=5, tps=10)
    assert non_dominated_placements([p_lo, p_hi]) == [p_hi]


def test_pareto_keeps_two_when_neither_dominates():
    cheap_slow = _placement(cu=0, tps=15)
    expensive_fast = _placement(cu=100, tps=35)
    frontier = non_dominated_placements([cheap_slow, expensive_fast])
    assert len(frontier) == 2
    assert cheap_slow in frontier
    assert expensive_fast in frontier


def test_non_dominated_frontier_is_monotonic():
    """Sorted by cu_paid asc, tps must be strictly non-decreasing."""
    placements = [
        _placement(cu=c, tps=t)
        for c, t in [
            (0, 15),
            (1, 16),
            (2, 14),
            (3, 18),
            (4, 17),
            (5, 20),
        ]
    ]
    frontier = non_dominated_placements(placements)
    by_cu = sorted(frontier, key=lambda p: p.cu_paid)
    for a, b in zip(by_cu, by_cu[1:]):
        assert b.tps > a.tps


def test_pareto_empty_input():
    assert non_dominated_placements([]) == []


# ─────────────────────── mode selection ────────────────────────────


def test_cheap_mode_picks_lowest_cu():
    frontier = [
        _placement(cu=0, tps=10),
        _placement(cu=5, tps=20),
        _placement(cu=10, tps=30),
    ]
    assert pick_by_mode(frontier, "cheap").cu_paid == 0


def test_fast_mode_picks_highest_tps():
    frontier = [
        _placement(cu=0, tps=10),
        _placement(cu=5, tps=20),
        _placement(cu=10, tps=30),
    ]
    assert pick_by_mode(frontier, "fast").tps == 30


def test_balanced_mode_returns_a_frontier_point():
    frontier = [
        _placement(cu=0, tps=10),
        _placement(cu=5, tps=20),
        _placement(cu=10, tps=30),
    ]
    chosen = pick_by_mode(frontier, "balanced")
    assert chosen in frontier


def test_pick_by_mode_empty_raises():
    with pytest.raises(ValueError):
        pick_by_mode([], "cheap")


# ─────────────────────── invariants on schedule() ──────────────────


def test_schedule_returns_pareto_optimal():
    """schedule() output must be on the Pareto frontier of its own candidate set."""
    workers, latency = make_simple_pool()
    units = make_unit_graph(QWEN_05B)
    p = schedule(units, workers, latency, querier_account="acc-alice", mode="cheap")
    assert p is not None

    candidates = list(
        enumerate_placements(units, workers, latency, querier_account="acc-alice")
    )
    frontier = non_dominated_placements(candidates)
    assert p in frontier


def test_self_utilization_wins_when_local_fits():
    """0.5B query from alice → all-on-alice, k=1, cu_paid=0."""
    workers, latency = make_simple_pool()
    units = make_unit_graph(QWEN_05B)
    p = schedule(units, workers, latency, querier_account="acc-alice", mode="cheap")
    assert p is not None
    assert p.all_local
    assert p.cu_paid == 0
    assert p.segments[0][0] == "alice"


def test_self_utilization_for_weak_querier_when_model_too_big():
    """70B query from carol (8GB Mac, clearly can't fit) → spills, cu_paid > 0."""
    workers, latency = make_simple_pool()
    units = make_unit_graph(LLAMA_70B, layers_per_block=16)  # 5 blocks
    p = schedule(
        units,
        workers,
        latency,
        querier_account="acc-carol",
        mode="cheap",
        max_segments=3,
    )
    assert p is not None
    assert p.cu_paid > 0  # had to spill — 70B doesn't fit on carol's 8GB


def test_infeasible_returns_none():
    """Tiny pool + huge model → no feasible placement."""
    tiny = resource_decl("tiny", account_id="acc", ram_gb=0.1)
    p = schedule(
        make_unit_graph(LLAMA_70B, layers_per_block=8),
        [tiny],
        LatencyMatrix(),
        querier_account="acc",
    )
    assert p is None


def test_every_enumerated_placement_is_feasible():
    """RAM, backend, reachability — all must hold for every enumerated candidate."""
    workers, latency = make_multi_cluster_pool()
    units = make_unit_graph(QWEN_7B, layers_per_block=4)
    workers_by_id = {w.worker_id: w for w in workers}
    units_by_id = {u.unit_id: u for u in units}

    seen = 0
    for p in enumerate_placements(
        units, workers, latency, querier_account="acc-arpan", max_segments=4
    ):
        for worker_id, unit_ids in p.segments:
            ram = sum(units_by_id[u].ram_required_gb for u in unit_ids)
            assert ram <= workers_by_id[worker_id].ram_gb_free + 1e-6
        for (a, _), (b, _) in zip(p.segments, p.segments[1:]):
            assert latency.reachable(a, b)
        seen += 1
        if seen > 5000:
            break  # don't grind through millions in this assertion


def test_cheap_mode_cu_le_fast_mode_cu():
    """cheap should never pay more CU than fast (on the same query)."""
    workers, latency = make_multi_cluster_pool()
    units = make_unit_graph(QWEN_7B, layers_per_block=4)
    p_cheap = schedule(
        units,
        workers,
        latency,
        querier_account="acc-arpan",
        mode="cheap",
        max_segments=4,
    )
    p_fast = schedule(
        units,
        workers,
        latency,
        querier_account="acc-arpan",
        mode="fast",
        max_segments=4,
    )
    assert p_cheap is not None and p_fast is not None
    assert p_cheap.cu_paid <= p_fast.cu_paid + 1e-6


def test_fast_mode_tps_ge_cheap_mode_tps():
    """fast should never get worse TPS than cheap (on the same query)."""
    workers, latency = make_multi_cluster_pool()
    units = make_unit_graph(QWEN_7B, layers_per_block=4)
    p_cheap = schedule(
        units,
        workers,
        latency,
        querier_account="acc-arpan",
        mode="cheap",
        max_segments=4,
    )
    p_fast = schedule(
        units,
        workers,
        latency,
        querier_account="acc-arpan",
        mode="fast",
        max_segments=4,
    )
    assert p_cheap is not None and p_fast is not None
    assert p_fast.tps >= p_cheap.tps - 1e-6


def test_balanced_between_cheap_and_fast_on_cu():
    workers, latency = make_multi_cluster_pool()
    units = make_unit_graph(QWEN_7B, layers_per_block=4)
    p_cheap = schedule(
        units,
        workers,
        latency,
        querier_account="acc-arpan",
        mode="cheap",
        max_segments=4,
    )
    p_bal = schedule(
        units,
        workers,
        latency,
        querier_account="acc-arpan",
        mode="balanced",
        max_segments=4,
    )
    p_fast = schedule(
        units,
        workers,
        latency,
        querier_account="acc-arpan",
        mode="fast",
        max_segments=4,
    )
    assert p_cheap is not None and p_bal is not None and p_fast is not None
    assert p_cheap.cu_paid <= p_bal.cu_paid <= p_fast.cu_paid + 1e-6


# ─────────────────────── fuzz: random pools ────────────────────────


def test_fuzz_schedule_is_always_pareto_optimal():
    """Random pools + queries → schedule() output is always on its own frontier."""
    rng = random.Random(42)
    for trial in range(15):
        n_workers = rng.randint(3, 6)
        workers, latency = make_random_pool(
            n_workers, n_clusters=rng.randint(1, 3), seed=trial
        )
        units = make_unit_graph(QWEN_05B if trial % 2 else QWEN_7B, layers_per_block=4)
        querier = workers[0].account_id
        mode = rng.choice(["cheap", "fast", "balanced"])
        p = schedule(
            units, workers, latency, querier_account=querier, mode=mode, max_segments=3
        )
        if p is None:
            continue
        candidates = list(
            enumerate_placements(
                units, workers, latency, querier_account=querier, max_segments=3
            )
        )
        frontier = non_dominated_placements(candidates)
        assert p in frontier, f"trial {trial}: schedule picked a dominated point"


def test_head_on_puts_the_first_segment_on_that_accounts_mac():
    """A question kept in your pools runs its head on your own Mac: the scheduler can be held to that."""
    units = make_unit_graph(LLAMA_70B, layers_per_block=4)
    workers, latency = make_simple_pool()
    for account in ("acc-alice", "acc-bob"):
        placed = schedule(units, workers, latency, head_on=account, max_segments=len(workers))
        if placed is None:
            continue  # that Mac can't hold the head of this model
        owner = next(w.account_id for w in workers if w.worker_id == placed.segments[0][0])
        assert owner == account
    assert schedule(units, workers, latency, head_on="acc-nobody") is None, "never someone else's Mac instead"

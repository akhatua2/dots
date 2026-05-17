"""Pure-Python tests for `dots.hub.plan_layers`.

The planner is the bit of hub logic that converts free-RAM weights and
optional per-worker layer caps into a contiguous layer assignment. Worth
testing directly so we don't have to spin up a hub + workers for cases
that are easier to verify with arithmetic.
"""
from __future__ import annotations

from dots.hub import plan_layers


def test_proportional_split_no_caps():
    """With no caps, layers distribute proportional to free-RAM weights."""
    ranges = plan_layers([1.0, 3.0], n_layers=24)
    assert ranges is not None
    (s0, e0), (s1, e1) = ranges
    # Total covered
    assert s0 == 0 and e1 == 24
    # Continuous
    assert e0 == s1
    # Heavier worker (weight 3) gets ~3x the work of the lighter (weight 1)
    # — exact split is 6/18 here.
    assert e0 - s0 == 6
    assert e1 - s1 == 18


def test_caps_respected_when_feasible():
    """If one worker has a cap, the planner doesn't exceed it."""
    # Two workers with equal free RAM, but worker 0 only commits 4 layers.
    ranges = plan_layers([1.0, 1.0], n_layers=12, caps=[4, 12])
    assert ranges is not None
    (s0, e0), (s1, e1) = ranges
    assert e0 - s0 == 4               # honored cap
    assert e1 - s1 == 8               # picked up the slack
    assert s0 == 0 and e1 == 12


def test_returns_none_when_total_capacity_below_n_layers():
    """If sum(caps) < n_layers the model can't be distributed."""
    # Two workers, each capped at 5 layers — total 10 — but model needs 24.
    ranges = plan_layers([1.0, 1.0], n_layers=24, caps=[5, 5])
    assert ranges is None


def test_returns_none_when_caps_sum_exactly_equals_n_layers():
    """Boundary: sum(caps) == n_layers should succeed and saturate everyone."""
    ranges = plan_layers([1.0, 1.0], n_layers=10, caps=[5, 5])
    assert ranges == [(0, 5), (5, 10)]


def test_uneven_weights_with_caps():
    """High-RAM worker still gets more, but its cap can keep it from
    swallowing everything."""
    ranges = plan_layers([1.0, 10.0], n_layers=20, caps=[20, 12])
    assert ranges is not None
    (_, e0), (s1, e1) = ranges
    # Heavy worker would naturally get ~18 but is capped at 12.
    assert e1 - s1 == 12
    # Light worker absorbs the remainder.
    assert e0 == 8


def test_single_worker_takes_all_layers():
    ranges = plan_layers([1.0], n_layers=24)
    assert ranges == [(0, 24)]


def test_single_worker_blocked_by_cap():
    ranges = plan_layers([1.0], n_layers=24, caps=[10])
    assert ranges is None

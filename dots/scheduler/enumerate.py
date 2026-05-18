"""Enumerate feasible placements of work units onto workers.

v1 enumerates contiguous segmentations of the unit chain × permutations
of workers. This is exponential in unit count and worker count, but for
the pool sizes dots targets (≤ ~20 workers, ≤ ~30 unit blocks with
layers_per_block grouping) it's tractable in well under a second.

A smarter algorithm (DP over the chain; ILP for big pools) is a follow-up
when we have real workload telemetry to validate against — see
resource-scheduling.md.
"""
from __future__ import annotations

from itertools import combinations, permutations
from typing import Iterator, Sequence

from .types import LatencyMatrix, Placement, ResourceDecl, WorkUnit

_EPS = 1e-6


def _segmentations(n: int, k: int) -> Iterator[list[tuple[int, int]]]:
    """Yield all contiguous partitions of [0, n) into exactly k non-empty parts."""
    if k <= 0 or k > n:
        return
    if k == 1:
        yield [(0, n)]
        return
    for splits in combinations(range(1, n), k - 1):
        ranges: list[tuple[int, int]] = []
        prev = 0
        for s in splits:
            ranges.append((prev, s))
            prev = s
        ranges.append((prev, n))
        yield ranges


def enumerate_placements(
    units: Sequence[WorkUnit],
    workers: Sequence[ResourceDecl],
    latency: LatencyMatrix,
    querier_account: str | None = None,
    max_segments: int | None = None,
) -> Iterator[Placement]:
    """Yield every feasible placement of `units` onto `workers`.

    `max_segments` caps the search at k segments. Useful for bounded
    runtime in tests and simulations.
    """
    n = len(units)
    if n == 0 or not workers:
        return
    workers_by_id = {w.worker_id: w for w in workers}
    units_by_id = {u.unit_id: u for u in units}

    upper_k = min(n, len(workers))
    if max_segments is not None:
        upper_k = min(upper_k, max_segments)

    # Late import to avoid circular dep (api.py imports types + this).
    from .api import _score

    for k in range(1, upper_k + 1):
        for ranges in _segmentations(n, k):
            for worker_perm in permutations(workers, k):
                placement = _try_build(
                    units, ranges, worker_perm,
                    workers_by_id, units_by_id, latency,
                    querier_account, _score,
                )
                if placement is not None:
                    yield placement


def _try_build(
    units: Sequence[WorkUnit],
    ranges: list[tuple[int, int]],
    worker_perm: Sequence[ResourceDecl],
    workers_by_id: dict[str, ResourceDecl],
    units_by_id: dict[str, WorkUnit],
    latency: LatencyMatrix,
    querier_account: str | None,
    score_fn,
) -> Placement | None:
    """Build a Placement if every constraint holds, else None."""
    segments: list[tuple[str, tuple[str, ...]]] = []
    for (start, end), worker in zip(ranges, worker_perm):
        segment_units = units[start:end]
        ram_needed = sum(u.ram_required_gb for u in segment_units)
        if ram_needed > worker.ram_gb_free + _EPS:
            return None
        for u in segment_units:
            if u.backend_required is not None and not (u.backend_required & worker.backends):
                return None
        segments.append((worker.worker_id, tuple(u.unit_id for u in segment_units)))

    for (w_a, _), (w_b, _) in zip(segments, segments[1:]):
        if not latency.reachable(w_a, w_b):
            return None

    cu, tps, compute_ms, network_ms = score_fn(
        segments, workers_by_id, units_by_id, latency, querier_account,
    )
    return Placement(
        segments=tuple(segments),
        cu_paid=cu, tps=tps,
        compute_time_ms=compute_ms,
        network_time_ms=network_ms,
    )

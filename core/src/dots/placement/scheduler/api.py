"""schedule() entry point + placement scoring.

The scoring model lives here because it's the bridge between
enumeration (which doesn't know about cost) and selection (which picks
on cost).
"""

from __future__ import annotations

import math
from typing import Sequence

from .enumerate import enumerate_placements
from .select import Mode, non_dominated_placements, pick_by_mode
from .types import LatencyMatrix, Placement, ResourceDecl, WorkUnit


def schedule(
    units: Sequence[WorkUnit],
    workers: Sequence[ResourceDecl],
    latency: LatencyMatrix,
    querier_account: str | None = None,
    mode: Mode = "cheap",
    max_segments: int | None = None,
    head_on: str | None = None,
) -> Placement | None:
    """Find a non-dominated placement of `units` over `workers`.

    Returns None if no feasible placement exists. Otherwise the returned
    placement is guaranteed to be non-dominated — no other feasible
    placement does better on both CU AND TPS.

    `head_on`: only placements whose first segment (the head, which reads
    the question and writes the answer) is on a worker of that account.
    """
    candidates = list(
        enumerate_placements(
            units,
            workers,
            latency,
            querier_account=querier_account,
            max_segments=max_segments,
        )
    )
    if head_on is not None:
        accounts = {w.worker_id: w.account_id for w in workers}
        candidates = [c for c in candidates if c.segments and accounts.get(c.segments[0][0]) == head_on]
    if not candidates:
        return None
    survivors = non_dominated_placements(candidates)
    return pick_by_mode(survivors, mode)


def _score(
    segments,
    workers_by_id,
    units_by_id,
    latency: LatencyMatrix,
    querier_account: str | None,
) -> tuple[float, float, float, float]:
    """Compute (cu_paid_per_token, tps, compute_ms, network_ms) for a placement.

    Compute cost = sum of unit FLOPs / worker throughput.
    Network cost = sum of inter-segment RTTs + loopback if applicable.
    CU = sum of unit FLOPs on workers whose account_id != querier_account.

    Kept separate from `enumerate` so `enumerate_placements` doesn't take
    a heavy dependency on the cost model — useful for future cost-model
    swaps (e.g. learned per-worker latency models).
    """
    compute_ms = 0.0
    cu_paid = 0.0
    for worker_id, unit_ids in segments:
        worker = workers_by_id[worker_id]
        segment_flops = sum(units_by_id[u].flops_per_token for u in unit_ids)
        if worker.compute_tflops_per_sec > 0:
            compute_ms += (
                segment_flops / (worker.compute_tflops_per_sec * 1e12) * 1000.0
            )
        else:
            compute_ms = math.inf
            break
        if querier_account is not None and worker.account_id != querier_account:
            cu_paid += segment_flops

    network_ms = 0.0
    for (w_a, _), (w_b, _) in zip(segments, segments[1:]):
        network_ms += latency.get(w_a, w_b)
    # Autoregressive loopback: sample's worker → embed's worker for next token.
    if len(segments) > 1:
        first_w = segments[0][0]
        last_w = segments[-1][0]
        if first_w != last_w:
            network_ms += latency.get(last_w, first_w)

    total_ms = compute_ms + network_ms
    tps = 1000.0 / total_ms if total_ms > 0 and math.isfinite(total_ms) else 0.0
    return cu_paid, tps, compute_ms, network_ms

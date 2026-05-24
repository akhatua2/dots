"""Small-scenario exhaustive placement helpers built on production scheduler."""

from __future__ import annotations

from dots.placement.scheduler import (
    LatencyMatrix,
    Placement,
    ResourceDecl,
    WorkUnit,
    enumerate_placements,
    non_dominated_placements,
    schedule,
)
from dots.backends.artifacts import BackendArtifactProfile

from .framework import SimModel, SimPool, SimWorker


def brute_force_static_frontier(
    pool: SimPool,
    model_id: str,
    *,
    user_id: str,
    unit_count: int,
    max_segments: int,
) -> tuple[list[Placement], Placement | None]:
    """Enumerate the Pareto frontier for one static model placement.

    This is suitable for small/medium tests only. It is intentionally built
    on the production scheduler primitives so simulation tests do not grow a
    separate optimizer.
    """
    if unit_count <= 0:
        raise ValueError("unit_count must be positive")
    model = pool.models[model_id]
    user = pool.users[user_id]
    artifact = pool._artifact(model_id)
    workers = _eligible_workers(pool, model, artifact)
    latency = _latency_matrix(pool, workers)
    units = _equal_model_units(model, artifact, unit_count)
    candidates = list(
        enumerate_placements(
            units,
            workers,
            latency,
            querier_account=user.account_id,
            max_segments=max_segments,
        )
    )
    frontier = non_dominated_placements(candidates)
    chosen = schedule(
        units,
        workers,
        latency,
        querier_account=user.account_id,
        mode="balanced",
        max_segments=max_segments,
    )
    return frontier, chosen


def _eligible_workers(
    pool: SimPool,
    model: SimModel,
    artifact: BackendArtifactProfile,
) -> list[ResourceDecl]:
    workers: list[ResourceDecl] = []
    for worker in pool.workers.values():
        if not worker.online:
            continue
        if artifact.backend not in worker.backends:
            continue
        if worker.disk_available_gb < _disk_required_for_cold_load(worker, artifact):
            continue
        workers.append(
            ResourceDecl(
                worker_id=worker.worker_id,
                account_id=worker.account_id,
                ram_gb_free=worker.ram_available_gb,
                compute_tflops_per_sec=worker.compute_tflops,
                backends=worker.backends,
                cluster_id="simulation",
                disk_gb_free=worker.disk_available_gb,
                cpu_utilization=0.0,
                io_read_gbps=worker.disk_read_gb_per_s,
                io_write_gbps=worker.disk_write_gb_per_s,
            )
        )
    return workers


def _disk_required_for_cold_load(
    worker: SimWorker,
    artifact: BackendArtifactProfile,
) -> float:
    key = (artifact.model_id, artifact.backend)
    return artifact.disk_gb_for_load(
        has_source=key in worker.source_artifacts,
        has_prepared=key in worker.prepared_artifacts,
    )


def _equal_model_units(
    model: SimModel,
    artifact: BackendArtifactProfile,
    unit_count: int,
) -> list[WorkUnit]:
    return [
        WorkUnit(
            unit_id=f"unit_{index}",
            ram_required_gb=artifact.resident_ram_gb / unit_count,
            flops_per_token=(2 * model.active_params) / unit_count,
            backend_required=frozenset({artifact.backend}),
        )
        for index in range(unit_count)
    ]


def _latency_matrix(pool: SimPool, workers: list[ResourceDecl]) -> LatencyMatrix:
    by_id = {worker.worker_id: worker for worker in workers}
    rtt: dict[tuple[str, str], float] = {}
    for left_id in by_id:
        for right_id in by_id:
            if left_id >= right_id:
                continue
            left = pool.workers[left_id]
            right = pool.workers[right_id]
            left_rtt = left.peer_rtt_ms.get(right_id)
            right_rtt = right.peer_rtt_ms.get(left_id)
            if left_rtt is None and right_rtt is None:
                continue
            if left_rtt is None or right_rtt is None:
                raise ValueError(f"incomplete peer latency mesh: {left_id}:{right_id}")
            if abs(left_rtt - right_rtt) > 1e-9:
                raise ValueError(f"asymmetric peer latency mesh: {left_id}:{right_id}")
            rtt[(left_id, right_id)] = left_rtt
    return LatencyMatrix(rtt_ms=rtt)

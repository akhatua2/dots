"""Backend-independent serving policy.

The functions here are intentionally pure. They do not know about sockets,
MLX, local files, or the simulation engine; callers pass worker snapshots and
artifact profiles, and the policy returns worker ids or booleans.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..backends.artifacts import BackendArtifactProfile

PRESSURE_REPLICA_ACTIVE_TURNS = 2.5
SELF_OWNED_TPS_FLOOR_RATIO = 0.75


@dataclass(frozen=True)
class ServingWorkerChoice:
    worker_id: str
    account_id: str
    active_turns: int
    tps: float


@dataclass(frozen=True)
class ServingWorkerSnapshot:
    worker_id: str
    account_id: str
    ram_available_gb: float
    ram_reserved_gb: float
    disk_available_gb: float
    evictable_cache_gb: float
    compute_tflops: float
    memory_bandwidth_gb_per_s: float
    download_gb_per_s: float
    disk_read_gb_per_s: float
    hub_rtt_ms: float
    supports_backend: bool
    has_active_load: bool
    has_loaded_model: bool
    has_source_artifact: bool
    has_prepared_artifact: bool


def choose_serving_worker(
    workers: Iterable[ServingWorkerChoice],
    *,
    querier_account: str,
) -> str | None:
    scored = [
        (
            worker.tps,
            worker.account_id == querier_account,
            -worker.active_turns,
            worker.worker_id,
            worker.worker_id,
        )
        for worker in workers
    ]
    if not scored:
        return None
    best_tps = max(score[0] for score in scored)
    self_owned = [
        score
        for score in scored
        if score[1] and score[0] >= best_tps * SELF_OWNED_TPS_FLOOR_RATIO
    ]
    if self_owned:
        return max(self_owned)[4]
    return max(scored)[4]


def should_start_pressure_replica(
    *,
    active_load_exists: bool,
    ready_replica_count: int,
    ready_active_turns: int,
    resident_ram_gb: float,
    pressure_turns_per_replica: float = PRESSURE_REPLICA_ACTIVE_TURNS,
) -> bool:
    if active_load_exists:
        return False
    if ready_replica_count >= replica_limit_for_resident_gb(resident_ram_gb):
        return False
    replica_floor = replica_floor_for_resident_gb(resident_ram_gb)
    pressure_threshold = ready_replica_count * pressure_turns_per_replica
    return (
        ready_replica_count < replica_floor or ready_active_turns >= pressure_threshold
    )


def replica_floor_for_resident_gb(resident_gb: float) -> int:
    if resident_gb <= 1.0:
        return 48
    if resident_gb <= 2.0:
        return 40
    if resident_gb <= 5.0:
        return 32
    if resident_gb <= 10.0:
        return 24
    if resident_gb <= 20.0:
        return 18
    if resident_gb <= 50.0:
        return 12
    return 3


def replica_limit_for_resident_gb(resident_gb: float) -> int:
    return replica_floor_for_resident_gb(resident_gb) * 2


def choose_load_worker(
    workers: Iterable[ServingWorkerSnapshot],
    artifact: BackendArtifactProfile,
    *,
    user_account: str,
    exclude_worker_ids: set[str] | None = None,
) -> str | None:
    excluded = exclude_worker_ids or set()
    candidates: list[tuple[float, str, str]] = []
    for worker in workers:
        if worker.worker_id in excluded:
            continue
        if worker.has_active_load:
            continue
        if worker.has_loaded_model:
            continue
        if not worker.supports_backend:
            continue
        disk_needed = artifact.disk_gb_for_load(
            has_source=worker.has_source_artifact,
            has_prepared=worker.has_prepared_artifact,
        )
        if worker.ram_available_gb < artifact.resident_ram_gb:
            continue
        if worker.disk_available_gb + worker.evictable_cache_gb < disk_needed:
            continue
        score = (
            (500.0 if worker.account_id == user_account else 0.0)
            + worker.compute_tflops * 12.0
            + worker.memory_bandwidth_gb_per_s / 30.0
            + worker.download_gb_per_s * 35.0
            + worker.disk_read_gb_per_s * 3.0
            - worker.hub_rtt_ms * 0.3
            - worker.ram_reserved_gb * 0.05
        )
        candidates.append((score, worker.worker_id, worker.worker_id))
    if not candidates:
        return None
    return max(candidates)[2]

"""Deterministic dynamic-pool simulator for tests.

This module is intentionally test-only. It models resource accounting,
virtual time, model preparation, turns, satisfaction, and CU ledger effects
without importing MLX or touching real network/disk.
"""

from __future__ import annotations

import heapq
import itertools
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from dots.accounting.ledger import flops_to_microcu, microcu_to_cu
from dots.backends.artifacts import BackendArtifactProfile

EPSILON = 1e-12
TFLOPS_TO_FLOPS_PER_S = 1_000_000_000_000
MILLISECONDS_PER_SECOND = 1_000

SimPhaseName = Literal[
    "backend_setup",
    "source_virtual_download",
    "source_cache_write",
    "source_read_for_conversion",
    "artifact_conversion",
    "prepared_artifact_write",
    "weight_load_read",
]
TurnStatus = Literal["active", "completed", "failed"]
DeploymentStatus = Literal["loading", "ready", "failed"]


class SimConstraintError(RuntimeError):
    """Raised when a simulated action violates a hard resource constraint."""


@dataclass(frozen=True)
class SimEvent:
    time_s: float
    kind: str
    detail: dict[str, object] = field(default_factory=dict)


@dataclass(order=True)
class _Scheduled:
    time_s: float
    sequence: int
    callback: Callable[[], None] = field(compare=False)


class SimClock:
    """Virtual clock that jumps between scheduled events."""

    def __init__(self) -> None:
        self.now_s = 0.0
        self._sequence = itertools.count()
        self._queue: list[_Scheduled] = []

    def schedule(self, delay_s: float, callback: Callable[[], None]) -> None:
        if delay_s < 0:
            raise ValueError("delay_s must be non-negative")
        heapq.heappush(
            self._queue,
            _Scheduled(self.now_s + delay_s, next(self._sequence), callback),
        )

    def run_until_idle(self, *, max_events: int = 1_000_000) -> int:
        events = 0
        while self._queue:
            if events >= max_events:
                raise RuntimeError("simulation exceeded max_events")
            scheduled = heapq.heappop(self._queue)
            self.now_s = scheduled.time_s
            scheduled.callback()
            events += 1
        return events

    def run_for(self, duration_s: float, *, max_events: int = 1_000_000) -> int:
        if duration_s < 0:
            raise ValueError("duration_s must be non-negative")
        end_s = self.now_s + duration_s
        events = 0
        while self._queue and self._queue[0].time_s <= end_s:
            if events >= max_events:
                raise RuntimeError("simulation exceeded max_events")
            scheduled = heapq.heappop(self._queue)
            self.now_s = scheduled.time_s
            scheduled.callback()
            events += 1
        self.now_s = end_s
        return events


@dataclass(frozen=True)
class SimModel:
    """Model facts independent of any worker/runtime packaging."""

    model_id: str
    repo_id: str
    architecture: str
    context_length: int
    hidden_size: int
    n_layers: int
    total_params: int
    active_params: int
    parameter_provenance: str
    is_moe: bool = False
    total_experts: int | None = None
    active_experts: int | None = None


@dataclass
class SimWorker:
    worker_id: str
    account_id: str
    owner_user_id: str | None
    location: str
    ram_gb: float
    disk_gb: float
    download_gb_per_s: float
    disk_read_gb_per_s: float
    disk_write_gb_per_s: float
    memory_bandwidth_gb_per_s: float
    compute_tflops: float
    hub_rtt_ms: float
    backends: frozenset[str]
    backend_setup_s: dict[str, float]
    conversion_gb_per_s: dict[str, float]
    peer_rtt_ms: dict[str, float] = field(default_factory=dict)
    peer_bandwidth_gb_per_s: dict[str, float] = field(default_factory=dict)
    online: bool = False
    online_since_s: float | None = None
    online_wall_s: float = 0.0
    ram_reserved_gb: float = 0.0
    disk_reserved_gb: float = 0.0
    busy_wall_s: float = 0.0
    active_turns: int = 0
    installed_backends: set[str] = field(default_factory=set)
    source_artifacts: set[tuple[str, str]] = field(default_factory=set)
    prepared_artifacts: set[tuple[str, str]] = field(default_factory=set)
    artifact_last_used_s: dict[tuple[str, str], float] = field(default_factory=dict)
    loaded_models: dict[str, str] = field(default_factory=dict)
    active_loads: set[int] = field(default_factory=set)

    @property
    def ram_available_gb(self) -> float:
        return self.ram_gb - self.ram_reserved_gb

    @property
    def disk_available_gb(self) -> float:
        return self.disk_gb - self.disk_reserved_gb


@dataclass(frozen=True)
class SimUser:
    user_id: str
    account_id: str
    location: str
    hub_rtt_ms: float
    target_tps: float
    target_ttft_s: float
    max_cu_per_turn: float


@dataclass
class SimLedgerAccount:
    account_id: str
    earned_microcu: int = 0
    spent_microcu: int = 0

    @property
    def earned_cu(self) -> float:
        return microcu_to_cu(self.earned_microcu)

    @property
    def spent_cu(self) -> float:
        return microcu_to_cu(self.spent_microcu)

    @property
    def balance_microcu(self) -> int:
        return self.earned_microcu - self.spent_microcu

    @property
    def balance_cu(self) -> float:
        return microcu_to_cu(self.balance_microcu)


@dataclass
class SimLedger:
    accounts: dict[str, SimLedgerAccount] = field(default_factory=dict)

    def account(self, account_id: str) -> SimLedgerAccount:
        account = self.accounts.get(account_id)
        if account is None:
            account = SimLedgerAccount(account_id)
            self.accounts[account_id] = account
        return account

    def record_turn(
        self,
        *,
        querier_account: str,
        provider_accounts: tuple[str, ...],
        total_microcu: int,
    ) -> None:
        if total_microcu < 0:
            raise ValueError("total_microcu must be non-negative")
        if total_microcu > 0 and not provider_accounts:
            raise ValueError("provider_accounts must be non-empty")

        credited: dict[str, int] = {}
        if provider_accounts:
            base_share = total_microcu // len(provider_accounts)
            remainder = total_microcu % len(provider_accounts)
            for index, account_id in enumerate(provider_accounts):
                share = base_share + (1 if index < remainder else 0)
                credited[account_id] = credited.get(account_id, 0) + share
        if sum(credited.values()) != total_microcu:
            raise ValueError("sim ledger must remain zero-sum per turn")

        self.account(querier_account).spent_microcu += total_microcu
        for account_id, microcu in credited.items():
            self.account(account_id).earned_microcu += microcu

    @property
    def total_balance_microcu(self) -> int:
        return sum(account.balance_microcu for account in self.accounts.values())


@dataclass(frozen=True)
class SimLoadPhase:
    name: SimPhaseName
    duration_s: float


@dataclass
class SimLoad:
    load_id: int
    worker_id: str
    model_id: str
    backend: str
    phases: tuple[SimLoadPhase, ...]
    started_at_s: float
    reserved_ram_gb: float
    reserved_disk_gb: float
    had_source: bool
    had_prepared: bool
    completed_at_s: float | None = None
    canceled: bool = False


@dataclass
class SimDeployment:
    model_id: str
    status: DeploymentStatus = "loading"
    ready_worker_ids: set[str] = field(default_factory=set)
    started_at_s: float = 0.0
    ready_at_s: float | None = None


@dataclass
class SimSatisfaction:
    tps_score: float
    ttft_score: float
    cu_score: float
    value: float


@dataclass
class SimTurn:
    turn_id: int
    user_id: str
    model_id: str
    worker_id: str
    worker_ids: tuple[str, ...]
    input_tokens: int
    output_tokens: int
    tps: float
    ttft_s: float
    total_cu: float
    satisfaction: SimSatisfaction
    started_at_s: float
    completed_at_s: float
    status: TurnStatus = "active"


@dataclass(frozen=True)
class SimPoolMetrics:
    sim_time_s: float
    ram_utilization: float
    disk_utilization: float
    active_turns: int
    active_worker_utilization: float
    completed_turns: int
    failed_turns: int
    mean_satisfaction: float
    total_cu_spent: float
    mean_worker_busy_utilization: float
    peak_worker_busy_utilization: float
    worker_tps: dict[str, float]
    model_queue_pressure: dict[str, float]
    unused_ready_replicas: dict[str, int]
    replica_imbalance: dict[str, float]
    ledger_balances: dict[str, float]
    ledger_balance_microcu: dict[str, int]
    ledger_total_balance_microcu: int


@dataclass(frozen=True)
class SimMetricSample:
    time_s: float
    label: str
    metrics: SimPoolMetrics


class SimPool:
    def __init__(self, clock: SimClock | None = None) -> None:
        self.clock = clock or SimClock()
        self.models: dict[str, SimModel] = {}
        self.artifacts: dict[tuple[str, str], BackendArtifactProfile] = {}
        self.workers: dict[str, SimWorker] = {}
        self.users: dict[str, SimUser] = {}
        self.departed_users: set[str] = set()
        self.deployments: dict[str, SimDeployment] = {}
        self.loads: dict[int, SimLoad] = {}
        self.turns: dict[int, SimTurn] = {}
        self.ledger = SimLedger()
        self.event_log: list[SimEvent] = []
        self.metric_samples: list[SimMetricSample] = []
        self._load_ids = itertools.count(1)
        self._turn_ids = itertools.count(1)

    def log(self, kind: str, **detail: object) -> None:
        self.event_log.append(SimEvent(self.clock.now_s, kind, detail))

    def register_model(self, model: SimModel) -> None:
        if model.model_id in self.models:
            raise ValueError(f"model already registered: {model.model_id}")
        self.models[model.model_id] = model
        self.log("model_registered", model_id=model.model_id)

    def register_artifact(self, artifact: BackendArtifactProfile) -> None:
        if artifact.model_id not in self.models:
            raise ValueError(f"unknown model for artifact: {artifact.model_id}")
        key = (artifact.model_id, artifact.backend)
        if key in self.artifacts:
            raise ValueError(
                f"artifact already registered: {artifact.model_id}:{artifact.backend}"
            )
        self.artifacts[key] = artifact
        self.log(
            "artifact_registered",
            model_id=artifact.model_id,
            backend=artifact.backend,
        )

    def worker_join(self, worker: SimWorker) -> None:
        if worker.worker_id in self.workers and self.workers[worker.worker_id].online:
            raise ValueError(f"worker already online: {worker.worker_id}")
        worker.online = True
        worker.online_since_s = self.clock.now_s
        self.workers[worker.worker_id] = worker
        self.log(
            "worker_join",
            worker_id=worker.worker_id,
            account_id=worker.account_id,
            owner_user_id=worker.owner_user_id,
            location=worker.location,
        )

    def worker_leave(self, worker_id: str) -> None:
        worker = self.workers.get(worker_id)
        if worker is None or not worker.online:
            raise ValueError(f"worker is not online: {worker_id}")
        worker.online = False
        if worker.online_since_s is not None:
            worker.online_wall_s += max(0.0, self.clock.now_s - worker.online_since_s)
            worker.online_since_s = None
        for load_id in list(worker.active_loads):
            load = self.loads[load_id]
            self._cancel_load(worker, load)
        self._unload_worker_models(worker)
        for turn in self.turns.values():
            if worker_id in turn.worker_ids and turn.status == "active":
                turn.status = "failed"
                for turn_worker_id in turn.worker_ids:
                    turn_worker = self.workers.get(turn_worker_id)
                    if turn_worker is not None:
                        turn_worker.active_turns = max(
                            0,
                            turn_worker.active_turns - 1,
                        )
                self.log("turn_failed", turn_id=turn.turn_id, worker_id=worker_id)
        self.log("worker_leave", worker_id=worker_id)

    def user_join(self, user: SimUser) -> None:
        if user.user_id in self.users:
            raise ValueError(f"user already online: {user.user_id}")
        self.users[user.user_id] = user
        self.departed_users.discard(user.user_id)
        self.log(
            "user_join",
            user_id=user.user_id,
            account_id=user.account_id,
            location=user.location,
        )

    def user_leave(self, user_id: str) -> None:
        if user_id not in self.users:
            raise ValueError(f"user is not online: {user_id}")
        self.users.pop(user_id)
        self.departed_users.add(user_id)
        self.log("user_leave", user_id=user_id)

    def seed_source_artifact(
        self,
        worker_id: str,
        model_id: str,
        backend: str | None = None,
    ) -> None:
        worker = self._worker(worker_id)
        artifact = self._artifact(model_id, backend)
        key = (model_id, artifact.backend)
        if key not in worker.source_artifacts:
            self._reserve_disk(worker, artifact.source_gb)
            worker.source_artifacts.add(key)
            if artifact.source_is_prepared:
                worker.prepared_artifacts.add((model_id, artifact.backend))
        worker.artifact_last_used_s[key] = self.clock.now_s

    def seed_prepared_artifact(
        self,
        worker_id: str,
        model_id: str,
        backend: str | None = None,
    ) -> None:
        worker = self._worker(worker_id)
        artifact = self._artifact(model_id, backend)
        key = (model_id, artifact.backend)
        if key not in worker.prepared_artifacts:
            self._reserve_disk(worker, artifact.prepared_cache_gb())
            worker.prepared_artifacts.add(key)
        worker.artifact_last_used_s[key] = self.clock.now_s

    def seed_installed_backend(self, worker_id: str, backend: str) -> None:
        self._worker(worker_id).installed_backends.add(backend)

    def start_load(
        self,
        worker_id: str,
        model_id: str,
        *,
        backend: str | None = None,
    ) -> SimLoad:
        worker = self._worker(worker_id)
        model = self._model(model_id)
        artifact = self._artifact(model_id, backend)
        selected_backend = artifact.backend
        if not worker.online:
            raise SimConstraintError(f"worker is offline: {worker_id}")
        if selected_backend not in worker.backends:
            raise SimConstraintError(
                f"worker {worker_id} does not support backend {selected_backend}"
            )
        if worker.loaded_models.get(model_id) == selected_backend:
            deployment = self._deployment(model_id)
            deployment.ready_worker_ids.add(worker_id)
            deployment.status = "ready"
            deployment.ready_at_s = self.clock.now_s
            return SimLoad(
                load_id=next(self._load_ids),
                worker_id=worker_id,
                model_id=model_id,
                backend=selected_backend,
                phases=(),
                started_at_s=self.clock.now_s,
                reserved_ram_gb=0.0,
                reserved_disk_gb=0.0,
                had_source=True,
                had_prepared=True,
                completed_at_s=self.clock.now_s,
            )

        phases = self._load_phases(worker, model, artifact)
        key = (model_id, selected_backend)
        had_source = key in worker.source_artifacts
        had_prepared = key in worker.prepared_artifacts
        disk_needed = artifact.disk_gb_for_load(
            has_source=had_source,
            has_prepared=had_prepared,
        )
        self._reserve_ram(worker, artifact.resident_ram_gb)
        try:
            if worker.disk_available_gb < disk_needed:
                self.reap_cache(worker.worker_id, required_free_gb=disk_needed)
            self._reserve_disk(worker, disk_needed)
        except Exception:
            worker.ram_reserved_gb = max(
                0.0,
                worker.ram_reserved_gb - artifact.resident_ram_gb,
            )
            raise

        load = SimLoad(
            load_id=next(self._load_ids),
            worker_id=worker_id,
            model_id=model_id,
            backend=selected_backend,
            phases=tuple(phases),
            started_at_s=self.clock.now_s,
            reserved_ram_gb=artifact.resident_ram_gb,
            reserved_disk_gb=disk_needed,
            had_source=had_source,
            had_prepared=had_prepared,
        )
        self.loads[load.load_id] = load
        worker.active_loads.add(load.load_id)
        deployment = self._deployment(model_id)
        deployment.status = "loading"
        self.log(
            "load_started",
            worker_id=worker_id,
            model_id=model_id,
            load_id=load.load_id,
        )
        self._schedule_load_phase(load, phase_index=0)
        return load

    def start_turn(
        self,
        user_id: str,
        model_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        worker_id: str | None = None,
    ) -> SimTurn:
        user = self._user(user_id)
        model = self._model(model_id)
        worker = self._worker(
            worker_id or self._first_ready_worker_id(model_id),
        )
        if not worker.online:
            raise SimConstraintError(f"worker is offline: {worker.worker_id}")
        if model_id not in worker.loaded_models:
            raise SimConstraintError(f"worker has not loaded model: {model_id}")
        artifact = self._loaded_artifact(worker, model_id)

        tps = self.effective_tps(
            worker.worker_id,
            model_id,
            user_id=user_id,
            reserve_turn_slot=True,
        )
        if tps <= 0:
            raise SimConstraintError("effective TPS must be positive")
        ttft_s = self._ttft_s(worker, model, artifact, user)
        total_microcu = self._turn_microcu(model, input_tokens + output_tokens)
        total_cu = microcu_to_cu(total_microcu)
        satisfaction = self._satisfaction(user, tps, ttft_s, total_cu)
        duration_s = output_tokens / tps
        turn = SimTurn(
            turn_id=next(self._turn_ids),
            user_id=user_id,
            model_id=model_id,
            worker_id=worker.worker_id,
            worker_ids=(worker.worker_id,),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tps=tps,
            ttft_s=ttft_s,
            total_cu=total_cu,
            satisfaction=satisfaction,
            started_at_s=self.clock.now_s,
            completed_at_s=self.clock.now_s + duration_s,
        )
        self.turns[turn.turn_id] = turn
        worker.active_turns += 1
        worker.busy_wall_s += duration_s
        self.log(
            "turn_started",
            turn_id=turn.turn_id,
            user_id=user_id,
            worker_id=worker.worker_id,
            model_id=model_id,
        )

        def complete_turn() -> None:
            if turn.status != "active":
                return
            current_worker = self.workers.get(worker.worker_id)
            if current_worker is None or not current_worker.online:
                turn.status = "failed"
                self.log("turn_failed", turn_id=turn.turn_id)
                return
            turn.status = "completed"
            current_worker.active_turns = max(0, current_worker.active_turns - 1)
            self.ledger.record_turn(
                querier_account=user.account_id,
                provider_accounts=(current_worker.account_id,),
                total_microcu=total_microcu,
            )
            self.log("turn_completed", turn_id=turn.turn_id, total_cu=total_cu)

        self.clock.schedule(duration_s, complete_turn)
        return turn

    def start_sharded_turn(
        self,
        user_id: str,
        model_id: str,
        *,
        worker_ids: tuple[str, ...],
        input_tokens: int,
        output_tokens: int,
    ) -> SimTurn:
        if not worker_ids:
            raise ValueError("worker_ids must be non-empty")
        user = self._user(user_id)
        model = self._model(model_id)
        workers = [self._worker(worker_id) for worker_id in worker_ids]
        for worker in workers:
            if not worker.online:
                raise SimConstraintError(f"worker is offline: {worker.worker_id}")
            if model_id not in worker.loaded_models:
                raise SimConstraintError(
                    f"worker has not loaded model: {worker.worker_id}:{model_id}"
                )
        artifacts = [self._loaded_artifact(worker, model_id) for worker in workers]
        if len({artifact.backend for artifact in artifacts}) != 1:
            raise SimConstraintError("sharded turn requires one backend family")
        artifact = artifacts[0]

        tps = self.sharded_effective_tps(
            worker_ids,
            model_id,
            user_id=user_id,
            reserve_turn_slot=True,
        )
        if tps <= 0:
            raise SimConstraintError("effective TPS must be positive")
        ttft_s = self._sharded_ttft_s(workers, model, artifact, user)
        total_microcu = self._turn_microcu(model, input_tokens + output_tokens)
        total_cu = microcu_to_cu(total_microcu)
        satisfaction = self._satisfaction(user, tps, ttft_s, total_cu)
        duration_s = output_tokens / tps
        turn = SimTurn(
            turn_id=next(self._turn_ids),
            user_id=user_id,
            model_id=model_id,
            worker_id=workers[0].worker_id,
            worker_ids=tuple(worker.worker_id for worker in workers),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tps=tps,
            ttft_s=ttft_s,
            total_cu=total_cu,
            satisfaction=satisfaction,
            started_at_s=self.clock.now_s,
            completed_at_s=self.clock.now_s + duration_s,
        )
        self.turns[turn.turn_id] = turn
        for worker in workers:
            worker.active_turns += 1
            worker.busy_wall_s += duration_s
        self.log(
            "turn_started",
            turn_id=turn.turn_id,
            user_id=user_id,
            worker_ids=turn.worker_ids,
            model_id=model_id,
        )

        def complete_turn() -> None:
            if turn.status != "active":
                return
            for worker in workers:
                current_worker = self.workers.get(worker.worker_id)
                if current_worker is None or not current_worker.online:
                    turn.status = "failed"
                    self.log("turn_failed", turn_id=turn.turn_id)
                    return
            turn.status = "completed"
            for worker in workers:
                worker.active_turns = max(0, worker.active_turns - 1)
            self.ledger.record_turn(
                querier_account=user.account_id,
                provider_accounts=tuple(worker.account_id for worker in workers),
                total_microcu=total_microcu,
            )
            self.log("turn_completed", turn_id=turn.turn_id, total_cu=total_cu)

        self.clock.schedule(duration_s, complete_turn)
        return turn

    def effective_tps(
        self,
        worker_id: str,
        model_id: str,
        *,
        user_id: str | None = None,
        reserve_turn_slot: bool = False,
    ) -> float:
        worker = self._worker(worker_id)
        model = self._model(model_id)
        artifact = self._loaded_artifact(worker, model_id)
        compute_s = (
            2 * model.active_params / (worker.compute_tflops * TFLOPS_TO_FLOPS_PER_S)
        )
        memory_s = (
            artifact.weight_read_gb_per_token(
                total_params=model.total_params,
                active_params=model.active_params,
            )
            / worker.memory_bandwidth_gb_per_s
        )
        user = self.users[user_id] if user_id is not None else None
        user_network_s = (user.hub_rtt_ms / MILLISECONDS_PER_SECOND) if user else 0.0
        worker_network_s = worker.hub_rtt_ms / MILLISECONDS_PER_SECOND
        concurrent_turns = worker.active_turns + (1 if reserve_turn_slot else 0)
        local_s = max(compute_s, memory_s) * max(1, concurrent_turns)
        token_s = local_s + worker_network_s + user_network_s
        return 1.0 / token_s if token_s > 0 else 0.0

    def sharded_effective_tps(
        self,
        worker_ids: tuple[str, ...],
        model_id: str,
        *,
        user_id: str | None = None,
        reserve_turn_slot: bool = False,
    ) -> float:
        if not worker_ids:
            raise ValueError("worker_ids must be non-empty")
        if len(worker_ids) == 1:
            return self.effective_tps(
                worker_ids[0],
                model_id,
                user_id=user_id,
                reserve_turn_slot=reserve_turn_slot,
            )
        model = self._model(model_id)
        workers = [self._worker(worker_id) for worker_id in worker_ids]
        artifacts = [self._loaded_artifact(worker, model_id) for worker in workers]
        if len({artifact.backend for artifact in artifacts}) != 1:
            raise SimConstraintError("sharded TPS requires one backend family")
        artifact = artifacts[0]
        shard_count = len(workers)
        token_s = 0.0
        for worker in workers:
            compute_s = (
                2
                * (model.active_params / shard_count)
                / (worker.compute_tflops * TFLOPS_TO_FLOPS_PER_S)
            )
            memory_s = (
                artifact.weight_read_gb_per_token(
                    total_params=model.total_params,
                    active_params=model.active_params,
                )
                / shard_count
            ) / worker.memory_bandwidth_gb_per_s
            concurrent_turns = worker.active_turns + (1 if reserve_turn_slot else 0)
            token_s += max(compute_s, memory_s) * max(1, concurrent_turns)
        for left, right in zip(workers, workers[1:]):
            token_s += self._peer_transfer_s(left, right, model, artifact)
        token_s += self._peer_transfer_s(workers[-1], workers[0], model, artifact)
        user = self.users[user_id] if user_id is not None else None
        if user is not None:
            token_s += user.hub_rtt_ms / MILLISECONDS_PER_SECOND
        token_s += workers[0].hub_rtt_ms / MILLISECONDS_PER_SECOND
        return 1.0 / token_s if token_s > 0 else 0.0

    def metrics(self) -> SimPoolMetrics:
        online_workers = [worker for worker in self.workers.values() if worker.online]
        total_ram = sum(worker.ram_gb for worker in online_workers)
        total_disk = sum(worker.disk_gb for worker in online_workers)
        used_ram = sum(worker.ram_reserved_gb for worker in online_workers)
        used_disk = sum(worker.disk_reserved_gb for worker in online_workers)
        busy_utilizations = [
            min(worker.busy_wall_s / online_wall_s, 1.0)
            for worker in self.workers.values()
            if (online_wall_s := self._worker_online_wall_s(worker)) > 0
        ]
        worker_tps: dict[str, float] = {}
        for worker in online_workers:
            for model_id in worker.loaded_models:
                worker_tps[f"{worker.worker_id}:{model_id}"] = self.effective_tps(
                    worker.worker_id,
                    model_id,
                )
        completed_turns = [
            turn for turn in self.turns.values() if turn.status == "completed"
        ]
        failed_turns = [turn for turn in self.turns.values() if turn.status == "failed"]
        active_turns = [turn for turn in self.turns.values() if turn.status == "active"]
        active_worker_utilization = (
            sum(1 for worker in online_workers if worker.active_turns > 0)
            / len(online_workers)
            if online_workers
            else 0.0
        )
        active_turns_by_model: dict[str, int] = defaultdict(int)
        served_worker_counts_by_model: dict[str, Counter[str]] = defaultdict(Counter)
        for turn in active_turns:
            active_turns_by_model[turn.model_id] += 1
        for turn in completed_turns:
            for worker_id in turn.worker_ids:
                served_worker_counts_by_model[turn.model_id][worker_id] += 1
        ready_worker_ids_by_model = {
            model_id: {
                worker_id
                for worker_id in deployment.ready_worker_ids
                if (worker := self.workers.get(worker_id)) is not None and worker.online
            }
            for model_id, deployment in self.deployments.items()
        }
        model_queue_pressure = {
            model_id: active_turns_by_model[model_id] / max(1, len(ready_worker_ids))
            for model_id, ready_worker_ids in ready_worker_ids_by_model.items()
        }
        unused_ready_replicas = {
            model_id: len(
                ready_worker_ids - set(served_worker_counts_by_model[model_id])
            )
            for model_id, ready_worker_ids in ready_worker_ids_by_model.items()
        }
        replica_imbalance = {
            model_id: self._gini(
                [
                    float(served_worker_counts_by_model[model_id][worker_id])
                    for worker_id in sorted(ready_worker_ids)
                ]
            )
            for model_id, ready_worker_ids in ready_worker_ids_by_model.items()
        }
        total_satisfaction = sum(turn.satisfaction.value for turn in completed_turns)
        total_cu_spent = sum(
            account.spent_cu for account in self.ledger.accounts.values()
        )
        return SimPoolMetrics(
            sim_time_s=self.clock.now_s,
            ram_utilization=used_ram / total_ram if total_ram > 0 else 0.0,
            disk_utilization=used_disk / total_disk if total_disk > 0 else 0.0,
            active_turns=len(active_turns),
            active_worker_utilization=active_worker_utilization,
            completed_turns=len(completed_turns),
            failed_turns=len(failed_turns),
            mean_satisfaction=(
                total_satisfaction / len(completed_turns) if completed_turns else 0.0
            ),
            total_cu_spent=total_cu_spent,
            mean_worker_busy_utilization=(
                sum(busy_utilizations) / len(busy_utilizations)
                if busy_utilizations
                else 0.0
            ),
            peak_worker_busy_utilization=(
                max(busy_utilizations) if busy_utilizations else 0.0
            ),
            worker_tps=worker_tps,
            model_queue_pressure=model_queue_pressure,
            unused_ready_replicas=unused_ready_replicas,
            replica_imbalance=replica_imbalance,
            ledger_balances={
                account_id: account.balance_cu
                for account_id, account in self.ledger.accounts.items()
            },
            ledger_balance_microcu={
                account_id: account.balance_microcu
                for account_id, account in self.ledger.accounts.items()
            },
            ledger_total_balance_microcu=self.ledger.total_balance_microcu,
        )

    def sample_metrics(self, label: str) -> SimPoolMetrics:
        metrics = self.metrics()
        self.metric_samples.append(SimMetricSample(self.clock.now_s, label, metrics))
        self.log("metrics_sampled", label=label)
        return metrics

    def _worker_online_wall_s(self, worker: SimWorker) -> float:
        online_wall_s = worker.online_wall_s
        if worker.online and worker.online_since_s is not None:
            online_wall_s += max(0.0, self.clock.now_s - worker.online_since_s)
        return online_wall_s

    def no_online_worker_exceeded_ram(self) -> bool:
        return all(
            worker.ram_reserved_gb <= worker.ram_gb + EPSILON
            for worker in self.workers.values()
            if worker.online
        )

    def no_online_worker_exceeded_disk(self) -> bool:
        return all(
            worker.disk_reserved_gb <= worker.disk_gb + EPSILON
            for worker in self.workers.values()
            if worker.online
        )

    def _schedule_load_phase(self, load: SimLoad, *, phase_index: int) -> None:
        if phase_index >= len(load.phases):
            self._complete_load(load)
            return
        phase = load.phases[phase_index]

        def finish_phase() -> None:
            worker = self.workers.get(load.worker_id)
            if load.canceled or worker is None or not worker.online:
                return
            self._apply_phase_side_effect(load, phase.name)
            self.log(
                "load_phase_done",
                worker_id=load.worker_id,
                model_id=load.model_id,
                load_id=load.load_id,
                phase=phase.name,
            )
            self._schedule_load_phase(load, phase_index=phase_index + 1)

        self.clock.schedule(phase.duration_s, finish_phase)

    def _complete_load(self, load: SimLoad) -> None:
        worker = self.workers.get(load.worker_id)
        if load.canceled or worker is None or not worker.online:
            return
        worker.active_loads.discard(load.load_id)
        worker.loaded_models[load.model_id] = load.backend
        worker.artifact_last_used_s[(load.model_id, load.backend)] = self.clock.now_s
        load.completed_at_s = self.clock.now_s
        deployment = self._deployment(load.model_id)
        deployment.status = "ready"
        deployment.ready_worker_ids.add(load.worker_id)
        deployment.ready_at_s = self.clock.now_s
        self.log(
            "deployment_ready",
            worker_id=load.worker_id,
            model_id=load.model_id,
            load_id=load.load_id,
        )

    def reap_cache(self, worker_id: str, *, required_free_gb: float) -> float:
        worker = self._worker(worker_id)
        freed_gb = 0.0
        while worker.disk_available_gb < required_free_gb:
            victim = self._oldest_evictable_artifact(worker)
            if victim is None:
                break
            artifact = self._artifact(*victim)
            if artifact.source_is_prepared:
                worker.source_artifacts.discard(victim)
                worker.prepared_artifacts.discard(victim)
                released_gb = artifact.source_gb
            elif victim in worker.prepared_artifacts:
                worker.prepared_artifacts.discard(victim)
                released_gb = artifact.prepared_gb
            else:
                worker.source_artifacts.discard(victim)
                released_gb = artifact.source_gb
            worker.artifact_last_used_s.pop(victim, None)
            worker.disk_reserved_gb = max(0.0, worker.disk_reserved_gb - released_gb)
            freed_gb += released_gb
            self.log(
                "cache_evicted",
                worker_id=worker.worker_id,
                model_id=victim[0],
                backend=victim[1],
                freed_gb=released_gb,
            )
        return freed_gb

    def evictable_cache_gb(self, worker_id: str) -> float:
        worker = self._worker(worker_id)
        return sum(
            self._evictable_artifact_gb(worker, key)
            for key in self._evictable_artifacts(worker)
        )

    def _cancel_load(self, worker: SimWorker, load: SimLoad) -> None:
        if load.canceled:
            return
        load.canceled = True
        worker.active_loads.discard(load.load_id)
        worker.ram_reserved_gb = max(0.0, worker.ram_reserved_gb - load.reserved_ram_gb)
        worker.disk_reserved_gb = max(
            0.0,
            worker.disk_reserved_gb - load.reserved_disk_gb,
        )
        key = (load.model_id, load.backend)
        if not load.had_source:
            worker.source_artifacts.discard(key)
        if not load.had_prepared:
            worker.prepared_artifacts.discard(key)
        self.log(
            "load_canceled",
            worker_id=worker.worker_id,
            model_id=load.model_id,
            load_id=load.load_id,
        )

    def _unload_worker_models(self, worker: SimWorker) -> None:
        for model_id, backend in list(worker.loaded_models.items()):
            artifact = self._artifact(model_id, backend)
            worker.ram_reserved_gb = max(
                0.0,
                worker.ram_reserved_gb - artifact.resident_ram_gb,
            )
            worker.loaded_models.pop(model_id, None)
            deployment = self.deployments.get(model_id)
            if deployment is not None:
                deployment.ready_worker_ids.discard(worker.worker_id)
                if not deployment.ready_worker_ids:
                    deployment.status = "loading"
                    deployment.ready_at_s = None
            self.log(
                "model_unloaded",
                worker_id=worker.worker_id,
                model_id=model_id,
                backend=backend,
            )

    def _oldest_evictable_artifact(
        self,
        worker: SimWorker,
    ) -> tuple[str, str] | None:
        artifacts = self._evictable_artifacts(worker)
        if not artifacts:
            return None
        return min(
            artifacts,
            key=lambda key: worker.artifact_last_used_s.get(key, 0.0),
        )

    def _evictable_artifacts(self, worker: SimWorker) -> list[tuple[str, str]]:
        active_load_keys = {
            (self.loads[load_id].model_id, self.loads[load_id].backend)
            for load_id in worker.active_loads
        }
        loaded_keys = set(worker.loaded_models.items())
        keys = worker.source_artifacts | worker.prepared_artifacts
        return [
            key
            for key in keys
            if key not in active_load_keys and key not in loaded_keys
        ]

    def _evictable_artifact_gb(
        self,
        worker: SimWorker,
        key: tuple[str, str],
    ) -> float:
        artifact = self._artifact(*key)
        if artifact.source_is_prepared:
            return artifact.source_gb
        if key in worker.prepared_artifacts:
            return artifact.prepared_gb
        return artifact.source_gb

    def _load_phases(
        self,
        worker: SimWorker,
        model: SimModel,
        artifact: BackendArtifactProfile,
    ) -> list[SimLoadPhase]:
        phases: list[SimLoadPhase] = []
        if artifact.backend not in worker.installed_backends:
            phases.append(
                SimLoadPhase(
                    "backend_setup",
                    worker.backend_setup_s[artifact.backend],
                )
            )
        source_key = (model.model_id, artifact.backend)
        if source_key not in worker.source_artifacts:
            phases.append(
                SimLoadPhase(
                    "source_virtual_download",
                    artifact.source_gb / worker.download_gb_per_s,
                )
            )
            phases.append(
                SimLoadPhase(
                    "source_cache_write",
                    artifact.source_gb / worker.disk_write_gb_per_s,
                )
            )
        prepared_key = (model.model_id, artifact.backend)
        if artifact.source_is_prepared:
            if (
                source_key in worker.source_artifacts
                and prepared_key not in worker.prepared_artifacts
            ):
                phases.append(SimLoadPhase("prepared_artifact_write", 0.0))
            phases.append(
                SimLoadPhase(
                    "weight_load_read",
                    artifact.resident_ram_gb / worker.disk_read_gb_per_s,
                )
            )
            return phases
        if prepared_key not in worker.prepared_artifacts:
            phases.append(
                SimLoadPhase(
                    "source_read_for_conversion",
                    artifact.source_gb / worker.disk_read_gb_per_s,
                )
            )
            phases.append(
                SimLoadPhase(
                    "artifact_conversion",
                    artifact.source_gb / worker.conversion_gb_per_s[artifact.backend],
                )
            )
            phases.append(
                SimLoadPhase(
                    "prepared_artifact_write",
                    artifact.prepared_gb / worker.disk_write_gb_per_s,
                )
            )
        phases.append(
            SimLoadPhase(
                "weight_load_read",
                artifact.resident_ram_gb / worker.disk_read_gb_per_s,
            )
        )
        return phases

    def _apply_phase_side_effect(self, load: SimLoad, phase: SimPhaseName) -> None:
        worker = self._worker(load.worker_id)
        if phase == "backend_setup":
            worker.installed_backends.add(load.backend)
        elif phase == "source_cache_write":
            worker.source_artifacts.add((load.model_id, load.backend))
            worker.artifact_last_used_s[(load.model_id, load.backend)] = (
                self.clock.now_s
            )
            artifact = self._artifact(load.model_id, load.backend)
            if artifact.source_is_prepared:
                worker.prepared_artifacts.add((load.model_id, load.backend))
        elif phase == "prepared_artifact_write":
            artifact = self._artifact(load.model_id, load.backend)
            worker.disk_reserved_gb = max(
                0.0,
                worker.disk_reserved_gb - artifact.scratch_gb,
            )
            worker.prepared_artifacts.add((load.model_id, load.backend))
            worker.artifact_last_used_s[(load.model_id, load.backend)] = (
                self.clock.now_s
            )

    @staticmethod
    def _gini(values: list[float]) -> float:
        if not values:
            return 0.0
        total = sum(values)
        if total <= 0:
            return 0.0
        ordered = sorted(values)
        weighted_sum = sum((index + 1) * value for index, value in enumerate(ordered))
        count = len(ordered)
        return (2.0 * weighted_sum) / (count * total) - (count + 1.0) / count

    @staticmethod
    def _reserve_ram(worker: SimWorker, amount_gb: float) -> None:
        if worker.ram_reserved_gb + amount_gb > worker.ram_gb + EPSILON:
            raise SimConstraintError(
                f"worker {worker.worker_id} RAM exceeded: "
                f"{worker.ram_reserved_gb + amount_gb:.3f} > {worker.ram_gb:.3f}"
            )
        worker.ram_reserved_gb += amount_gb

    @staticmethod
    def _reserve_disk(worker: SimWorker, amount_gb: float) -> None:
        if worker.disk_reserved_gb + amount_gb > worker.disk_gb + EPSILON:
            raise SimConstraintError(
                f"worker {worker.worker_id} disk exceeded: "
                f"{worker.disk_reserved_gb + amount_gb:.3f} > {worker.disk_gb:.3f}"
            )
        worker.disk_reserved_gb += amount_gb

    def _ttft_s(
        self,
        worker: SimWorker,
        model: SimModel,
        artifact: BackendArtifactProfile,
        user: SimUser,
    ) -> float:
        compute_s = (
            2 * model.active_params / (worker.compute_tflops * TFLOPS_TO_FLOPS_PER_S)
        )
        memory_s = (
            artifact.weight_read_gb_per_token(
                total_params=model.total_params,
                active_params=model.active_params,
            )
            / worker.memory_bandwidth_gb_per_s
        )
        return (user.hub_rtt_ms + worker.hub_rtt_ms) / MILLISECONDS_PER_SECOND + max(
            compute_s, memory_s
        )

    def _sharded_ttft_s(
        self,
        workers: list[SimWorker],
        model: SimModel,
        artifact: BackendArtifactProfile,
        user: SimUser,
    ) -> float:
        shard_count = len(workers)
        local_work_s = 0.0
        for worker in workers:
            compute_s = (
                2
                * (model.active_params / shard_count)
                / (worker.compute_tflops * TFLOPS_TO_FLOPS_PER_S)
            )
            memory_s = (
                artifact.weight_read_gb_per_token(
                    total_params=model.total_params,
                    active_params=model.active_params,
                )
                / shard_count
            ) / worker.memory_bandwidth_gb_per_s
            local_work_s += max(compute_s, memory_s)
        peer_s = sum(
            self._peer_transfer_s(left, right, model, artifact)
            for left, right in zip(workers, workers[1:])
        )
        return (
            (user.hub_rtt_ms + workers[0].hub_rtt_ms) / MILLISECONDS_PER_SECOND
            + local_work_s
            + peer_s
        )

    @staticmethod
    def _peer_transfer_s(
        left: SimWorker,
        right: SimWorker,
        model: SimModel,
        artifact: BackendArtifactProfile,
    ) -> float:
        rtt_ms = left.peer_rtt_ms[right.worker_id]
        bandwidth = left.peer_bandwidth_gb_per_s[right.worker_id]
        return rtt_ms / MILLISECONDS_PER_SECOND + (
            artifact.hidden_state_gb_per_token(hidden_size=model.hidden_size)
            / bandwidth
        )

    @staticmethod
    def _turn_microcu(model: SimModel, total_tokens: int) -> int:
        return flops_to_microcu(2 * model.active_params * total_tokens)

    @staticmethod
    def _turn_cu(model: SimModel, total_tokens: int) -> float:
        return microcu_to_cu(SimPool._turn_microcu(model, total_tokens))

    @staticmethod
    def _satisfaction(
        user: SimUser,
        tps: float,
        ttft_s: float,
        cu: float,
    ) -> SimSatisfaction:
        tps_score = min(tps / user.target_tps, 1.0)
        ttft_score = min(user.target_ttft_s / max(ttft_s, EPSILON), 1.0)
        cu_score = min(user.max_cu_per_turn / max(cu, EPSILON), 1.0)
        value = 0.5 * tps_score + 0.3 * ttft_score + 0.2 * cu_score
        return SimSatisfaction(tps_score, ttft_score, cu_score, value)

    def _first_ready_worker_id(self, model_id: str) -> str:
        deployment = self.deployments.get(model_id)
        if deployment is None or not deployment.ready_worker_ids:
            raise SimConstraintError(f"model is not ready: {model_id}")
        return sorted(deployment.ready_worker_ids)[0]

    def _deployment(self, model_id: str) -> SimDeployment:
        deployment = self.deployments.get(model_id)
        if deployment is None:
            deployment = SimDeployment(model_id=model_id, started_at_s=self.clock.now_s)
            self.deployments[model_id] = deployment
        return deployment

    def _model(self, model_id: str) -> SimModel:
        model = self.models.get(model_id)
        if model is None:
            raise ValueError(f"unknown model: {model_id}")
        return model

    def _artifact(
        self,
        model_id: str,
        backend: str | None = None,
    ) -> BackendArtifactProfile:
        if backend is not None:
            artifact = self.artifacts.get((model_id, backend))
            if artifact is None:
                raise ValueError(f"unknown artifact: {model_id}:{backend}")
            return artifact
        matches = [
            artifact
            for (artifact_model_id, _), artifact in sorted(self.artifacts.items())
            if artifact_model_id == model_id
        ]
        if not matches:
            raise ValueError(f"no artifacts registered for model: {model_id}")
        if len(matches) > 1:
            raise ValueError(f"backend is required for model: {model_id}")
        return matches[0]

    def _loaded_artifact(
        self,
        worker: SimWorker,
        model_id: str,
    ) -> BackendArtifactProfile:
        backend = worker.loaded_models.get(model_id)
        if backend is None:
            raise SimConstraintError(f"worker has not loaded model: {model_id}")
        return self._artifact(model_id, backend)

    def _worker(self, worker_id: str) -> SimWorker:
        worker = self.workers.get(worker_id)
        if worker is None:
            raise ValueError(f"unknown worker: {worker_id}")
        return worker

    def _user(self, user_id: str) -> SimUser:
        user = self.users.get(user_id)
        if user is None:
            raise ValueError(f"unknown user: {user_id}")
        return user

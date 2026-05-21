"""Reusable simulation scenarios that produce optimization metrics."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from dots.backends.artifacts import BackendArtifactProfile, gb_to_bytes
from dots.serving.policy import (
    ServingWorkerChoice,
    ServingWorkerSnapshot,
    choose_load_worker as policy_choose_load_worker,
    choose_serving_worker as policy_choose_serving_worker,
    replica_floor_for_resident_gb,
    replica_limit_for_resident_gb,
    should_start_pressure_replica,
)

from .framework import SimClock, SimModel, SimPool, SimUser, SimWorker
from .reports import SimRunReport, build_report, report_delta

SingleTurnStrategy = Literal["slow_local", "fast_remote"]
Persona = Literal[
    "workday_builder",
    "evening_hobbyist",
    "weekend_hobbyist",
    "research_lab",
    "batch_automation",
]
SECONDS_PER_HOUR = 3_600.0
SECONDS_PER_DAY = 86_400.0
SIMULATION_HORIZON_S = 7 * SECONDS_PER_DAY
PEAK_TRAFFIC_REQUESTS = 500_000
REQUEST_RETRY_S = 90.0
MAX_REQUEST_ATTEMPTS = 18
OVERLOAD_DEMAND_MULTIPLIER = 5.0
OVERLOAD_OUTPUT_TOKEN_MULTIPLIER = 3.0
EXTREME_PROVIDER_ACCOUNT = "acc-provider-whale"
FLASH_CROWD_MODEL_ID = "mlx-community/Qwen2.5-14B-Instruct-4bit"
UPGRADE_TARGET_MODEL_ID = "mlx-community/Qwen2.5-Coder-32B-Instruct-4bit"

MONDAY = 0 * SECONDS_PER_DAY
TUESDAY = 1 * SECONDS_PER_DAY
WEDNESDAY = 2 * SECONDS_PER_DAY
THURSDAY = 3 * SECONDS_PER_DAY
FRIDAY = 4 * SECONDS_PER_DAY
SATURDAY = 5 * SECONDS_PER_DAY
SUNDAY = 6 * SECONDS_PER_DAY


@dataclass(frozen=True)
class SimLocation:
    name: str
    region: str
    metro: str
    access: str
    hub_rtt_ms: float


@dataclass(frozen=True)
class SimPersonProfile:
    user_id: str
    account_id: str
    persona: Persona
    location: SimLocation
    machine_count: int


@dataclass(frozen=True)
class SimSession:
    join_s: float
    leave_s: float


@dataclass(frozen=True)
class TrafficWindow:
    label: str
    start_s: float
    end_s: float
    demand_multiplier: float


@dataclass
class IncidentContext:
    workers: list[SimWorker]
    worker_indices: dict[str, int]
    models: list[SimModel]
    artifacts_by_model: dict[str, BackendArtifactProfile]
    state: dict[str, object] = field(default_factory=dict)


IncidentHandler = Callable[[SimPool, IncidentContext, str], None]


@dataclass(frozen=True)
class SimIncident:
    label: str
    start_s: float
    end_s: float
    on_start: IncidentHandler | None = None
    on_finish: IncidentHandler | None = None


@dataclass(frozen=True)
class StrategyComparison:
    scenario: str
    baseline: SimRunReport
    candidate: SimRunReport
    delta: dict[str, float]

    @property
    def satisfaction_gap(self) -> float:
        return self.delta["mean_satisfaction"]

    @property
    def failure_gap(self) -> float:
        return self.delta["failed_turns"]


def model(
    model_id: str = "fixture-qwen-1b",
    *,
    repo_id: str | None = None,
    architecture: str = "qwen2",
    context_length: int = 32_768,
    hidden_size: int = 3584,
    n_layers: int = 28,
    total_params: int | None = None,
    active_params: int = 1_000_000_000,
    parameter_provenance: str = "scenario",
    is_moe: bool = False,
    total_experts: int | None = None,
    active_experts: int | None = None,
) -> SimModel:
    return SimModel(
        model_id=model_id,
        repo_id=repo_id or model_id,
        architecture=architecture,
        context_length=context_length,
        hidden_size=hidden_size,
        n_layers=n_layers,
        total_params=total_params or active_params,
        active_params=active_params,
        parameter_provenance=parameter_provenance,
        is_moe=is_moe,
        total_experts=total_experts,
        active_experts=active_experts,
    )


def mlx_artifact(
    model_id: str,
    *,
    source_gb: float,
    size_provenance: str,
) -> BackendArtifactProfile:
    """Build a pinned MLX artifact snapshot from repository metadata.

    MLX repos already contain backend-ready safetensors, so source and
    prepared artifact are the same virtual bytes. No scenario downloads
    these bytes; virtual load time is derived from this snapshot.
    """
    return BackendArtifactProfile(
        model_id=model_id,
        backend="mlx",
        source_bytes=gb_to_bytes(source_gb),
        prepared_bytes=gb_to_bytes(source_gb),
        scratch_bytes=0,
        resident_ram_bytes=gb_to_bytes(source_gb),
        wire_dtype_bytes=2,
        metadata_source=size_provenance,
        source_is_prepared=True,
    )


def measured_model_catalog() -> tuple[tuple[SimModel, BackendArtifactProfile], ...]:
    """Pinned real-model metadata snapshots for offline simulations.

    The model facts come from published config/model cards. Artifact sizes are
    the Hugging Face file tree totals for MLX repositories. Keeping the values
    here makes simulation deterministic and network-free.
    """
    return (
        (
            model(
                "mlx-community/Qwen2.5-0.5B-Instruct-4bit",
                architecture="qwen2",
                hidden_size=896,
                n_layers=24,
                total_params=500_000_000,
                active_params=500_000_000,
                parameter_provenance="HF config + model id",
            ),
            mlx_artifact(
                "mlx-community/Qwen2.5-0.5B-Instruct-4bit",
                source_gb=0.290,
                size_provenance="HF file tree total: 290 MB",
            ),
        ),
        (
            model(
                "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
                architecture="qwen2",
                hidden_size=1536,
                n_layers=28,
                total_params=1_500_000_000,
                active_params=1_500_000_000,
                parameter_provenance="HF-compatible config + model id",
            ),
            mlx_artifact(
                "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
                source_gb=0.880,
                size_provenance="HF file tree total: 880 MB",
            ),
        ),
        (
            model(
                "mlx-community/Qwen2.5-3B-Instruct-4bit",
                architecture="qwen2",
                hidden_size=2048,
                n_layers=36,
                total_params=3_000_000_000,
                active_params=3_000_000_000,
                parameter_provenance="HF config + model id",
            ),
            mlx_artifact(
                "mlx-community/Qwen2.5-3B-Instruct-4bit",
                source_gb=1.75,
                size_provenance="HF file tree total: 1.75 GB",
            ),
        ),
        (
            model(
                "mlx-community/Qwen2.5-7B-Instruct-4bit",
                architecture="qwen2",
                hidden_size=3584,
                n_layers=28,
                total_params=7_000_000_000,
                active_params=7_000_000_000,
                parameter_provenance="HF config + model id",
            ),
            mlx_artifact(
                "mlx-community/Qwen2.5-7B-Instruct-4bit",
                source_gb=4.30,
                size_provenance="HF file tree total: 4.3 GB",
            ),
        ),
        (
            model(
                "mlx-community/Qwen2.5-14B-Instruct-4bit",
                architecture="qwen2",
                hidden_size=5120,
                n_layers=48,
                total_params=14_000_000_000,
                active_params=14_000_000_000,
                parameter_provenance="HF config + model id",
            ),
            mlx_artifact(
                "mlx-community/Qwen2.5-14B-Instruct-4bit",
                source_gb=8.32,
                size_provenance="HF file tree total: 8.32 GB",
            ),
        ),
        (
            model(
                "mlx-community/Qwen2.5-32B-Instruct-4bit",
                architecture="qwen2",
                hidden_size=5120,
                n_layers=64,
                total_params=32_000_000_000,
                active_params=32_000_000_000,
                parameter_provenance="HF model card + Qwen2.5 config family",
            ),
            mlx_artifact(
                "mlx-community/Qwen2.5-32B-Instruct-4bit",
                source_gb=18.4,
                size_provenance="HF file tree total: 18.4 GB",
            ),
        ),
        (
            model(
                "mlx-community/Qwen2.5-72B-Instruct-4bit",
                architecture="qwen2",
                hidden_size=8192,
                n_layers=80,
                total_params=72_000_000_000,
                active_params=72_000_000_000,
                parameter_provenance="HF model card + Qwen2.5 config family",
            ),
            mlx_artifact(
                "mlx-community/Qwen2.5-72B-Instruct-4bit",
                source_gb=40.9,
                size_provenance="HF file tree total: 40.9 GB",
            ),
        ),
        (
            model(
                "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",
                architecture="qwen2",
                hidden_size=3584,
                n_layers=28,
                total_params=7_000_000_000,
                active_params=7_000_000_000,
                parameter_provenance="HF config-equivalent Qwen2.5 7B",
            ),
            mlx_artifact(
                "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",
                source_gb=4.30,
                size_provenance="HF file tree total: 4.3 GB",
            ),
        ),
        (
            model(
                "mlx-community/Qwen2.5-Coder-32B-Instruct-4bit",
                architecture="qwen2",
                hidden_size=5120,
                n_layers=64,
                total_params=32_000_000_000,
                active_params=32_000_000_000,
                parameter_provenance="HF model card + Qwen2.5 config family",
            ),
            mlx_artifact(
                "mlx-community/Qwen2.5-Coder-32B-Instruct-4bit",
                source_gb=18.4,
                size_provenance="HF model card size: 18.4 GB",
            ),
        ),
        (
            model(
                "mlx-community/Mixtral-8x7B-Instruct-v0.1-4bit",
                architecture="mixtral",
                hidden_size=4096,
                n_layers=32,
                total_params=47_000_000_000,
                active_params=13_000_000_000,
                parameter_provenance="HF model card + Mixtral architecture",
                is_moe=True,
                total_experts=8,
                active_experts=2,
            ),
            mlx_artifact(
                "mlx-community/Mixtral-8x7B-Instruct-v0.1-4bit",
                source_gb=26.3,
                size_provenance="HF model card size: 26.3 GB",
            ),
        ),
        (
            model(
                "mlx-community/Qwen3.6-35B-A3B-8bit",
                architecture="qwen3_5_moe",
                context_length=262_144,
                hidden_size=2048,
                n_layers=40,
                total_params=35_000_000_000,
                active_params=3_000_000_000,
                parameter_provenance="HF model card + config",
                is_moe=True,
                total_experts=256,
                active_experts=8,
            ),
            mlx_artifact(
                "mlx-community/Qwen3.6-35B-A3B-8bit",
                source_gb=37.7,
                size_provenance="HF file tree total: 37.7 GB",
            ),
        ),
        (
            model(
                "mlx-community/Kimi-K2.5",
                architecture="kimi_k25",
                context_length=262_144,
                hidden_size=7168,
                n_layers=61,
                total_params=1_000_000_000_000,
                active_params=32_000_000_000,
                parameter_provenance="Moonshot/HF model summary + config",
                is_moe=True,
                total_experts=384,
                active_experts=8,
            ),
            mlx_artifact(
                "mlx-community/Kimi-K2.5",
                source_gb=658.0,
                size_provenance="HF file tree total: 658 GB",
            ),
        ),
    )


def worker(
    worker_id: str,
    *,
    account_id: str | None = None,
    owner_user_id: str | None = None,
    location: str = "unknown",
    ram_gb: float = 32.0,
    disk_gb: float = 64.0,
    download_gb_per_s: float = 2.0,
    disk_read_gb_per_s: float = 4.0,
    disk_write_gb_per_s: float = 2.0,
    memory_bandwidth_gb_per_s: float = 100.0,
    compute_tflops: float = 2.0,
    hub_rtt_ms: float = 1.0,
    backends: frozenset[str] = frozenset({"mlx"}),
) -> SimWorker:
    return SimWorker(
        worker_id=worker_id,
        account_id=account_id or f"acc-{worker_id}",
        owner_user_id=owner_user_id,
        location=location,
        ram_gb=ram_gb,
        disk_gb=disk_gb,
        download_gb_per_s=download_gb_per_s,
        disk_read_gb_per_s=disk_read_gb_per_s,
        disk_write_gb_per_s=disk_write_gb_per_s,
        memory_bandwidth_gb_per_s=memory_bandwidth_gb_per_s,
        compute_tflops=compute_tflops,
        hub_rtt_ms=hub_rtt_ms,
        backends=backends,
        backend_setup_s={"mlx": 3.0},
        conversion_gb_per_s={"mlx": 2.0},
    )


def user(
    user_id: str,
    *,
    account_id: str | None = None,
    location: str = "unknown",
    hub_rtt_ms: float = 9.0,
    target_tps: float = 100.0,
    target_ttft_s: float = 0.5,
    max_cu_per_turn: float = 0.1,
) -> SimUser:
    return SimUser(
        user_id=user_id,
        account_id=account_id or f"acc-{user_id}",
        location=location,
        hub_rtt_ms=hub_rtt_ms,
        target_tps=target_tps,
        target_ttft_s=target_ttft_s,
        max_cu_per_turn=max_cu_per_turn,
    )


def run_single_turn_choice(strategy: SingleTurnStrategy) -> SimPool:
    """Same world, different serving choice.

    `slow_local` intentionally picks Bob's slower local machine.
    `fast_remote` picks Alice's faster already-loaded machine.
    The comparison report exposes the suboptimality gap.
    """
    pool = SimPool(SimClock())
    sim_model = model()
    sim_artifact = mlx_artifact(
        sim_model.model_id,
        source_gb=4.0,
        size_provenance="single-turn fixture artifact profile",
    )
    sim_user = user("bob")
    slow_local = worker(
        "bob-laptop",
        account_id=sim_user.account_id,
        compute_tflops=1.0,
        memory_bandwidth_gb_per_s=50.0,
        hub_rtt_ms=1.0,
    )
    fast_remote = worker(
        "alice-max",
        account_id="acc-alice",
        compute_tflops=8.0,
        memory_bandwidth_gb_per_s=600.0,
        hub_rtt_ms=15.0,
    )
    pool.register_model(sim_model)
    pool.register_artifact(sim_artifact)
    pool.users[sim_user.user_id] = sim_user
    for sim_worker in (slow_local, fast_remote):
        pool.worker_join(sim_worker)
        pool.seed_installed_backend(sim_worker.worker_id, sim_artifact.backend)
        pool.seed_source_artifact(
            sim_worker.worker_id,
            sim_model.model_id,
            sim_artifact.backend,
        )
        pool.seed_prepared_artifact(
            sim_worker.worker_id,
            sim_model.model_id,
            sim_artifact.backend,
        )
        pool.start_load(
            sim_worker.worker_id,
            sim_model.model_id,
            backend=sim_artifact.backend,
        )
    pool.clock.run_until_idle()

    chosen_worker = slow_local if strategy == "slow_local" else fast_remote
    pool.start_turn(
        sim_user.user_id,
        sim_model.model_id,
        input_tokens=10,
        output_tokens=80,
        worker_id=chosen_worker.worker_id,
    )
    pool.clock.run_until_idle()
    pool.sample_metrics(strategy)
    return pool


def compare_single_turn_choice() -> StrategyComparison:
    scenario = "single_turn_choice"
    baseline_pool = run_single_turn_choice("slow_local")
    candidate_pool = run_single_turn_choice("fast_remote")
    baseline = build_report(
        baseline_pool,
        scenario=scenario,
        strategy="slow_local",
    )
    candidate = build_report(
        candidate_pool,
        scenario=scenario,
        strategy="fast_remote",
    )
    return StrategyComparison(
        scenario=scenario,
        baseline=baseline,
        candidate=candidate,
        delta=report_delta(baseline, candidate),
    )


def run_large_dynamic_pool(
    *,
    n_workers: int = 200,
    n_users: int = 100,
    n_turns: int = PEAK_TRAFFIC_REQUESTS,
    n_models: int = 12,
) -> SimPool:
    pool = SimPool(SimClock())
    catalog = measured_model_catalog()[:n_models]
    if len(catalog) < 5:
        raise ValueError("dynamic simulation should include at least 5 models")
    for sim_model, artifact in catalog:
        pool.register_model(sim_model)
        pool.register_artifact(artifact)
    models = [sim_model for sim_model, _artifact in catalog]
    artifacts_by_model = {
        sim_model.model_id: artifact for sim_model, artifact in catalog
    }

    locations = _locations()
    people = _people(n_users, locations)
    sessions_by_user = {person.user_id: _person_sessions(person) for person in people}
    workers = _workers_for_people(n_workers, people, locations)
    _connect_peer_mesh(workers, {location.name: location for location in locations})
    people_by_user_id = {person.user_id: person for person in people}
    incident_context = IncidentContext(
        workers=workers,
        worker_indices={
            sim_worker.worker_id: index for index, sim_worker in enumerate(workers)
        },
        models=models,
        artifacts_by_model=artifacts_by_model,
    )
    incidents = _week_incidents()

    for person in people:
        sim_user = user(
            person.user_id,
            account_id=person.account_id,
            location=person.location.name,
            hub_rtt_ms=person.location.hub_rtt_ms,
            target_tps=35.0 + float((int(person.user_id[1:]) * 7) % 70),
            target_ttft_s=0.4 + float((int(person.user_id[1:]) * 5) % 12) / 10.0,
            max_cu_per_turn=0.08 + float((int(person.user_id[1:]) * 3) % 10) / 100.0,
        )
        for session in sessions_by_user[person.user_id]:
            pool.clock.schedule(
                session.join_s,
                lambda sim_user=sim_user: _safe_user_join(pool, sim_user),
            )
            if session.leave_s < SIMULATION_HORIZON_S:
                pool.clock.schedule(
                    session.leave_s,
                    lambda user_id=person.user_id: _safe_user_leave(pool, user_id),
                )

    for index, sim_worker in enumerate(workers):
        owner = (
            people_by_user_id[sim_worker.owner_user_id]
            if sim_worker.owner_user_id is not None
            else None
        )
        for session in _worker_sessions(index, sim_worker, owner):
            pool.clock.schedule(
                session.join_s,
                lambda sim_worker=sim_worker, index=index: _join_worker(
                    pool,
                    sim_worker,
                    index,
                    models,
                ),
            )
            if session.leave_s < SIMULATION_HORIZON_S:
                pool.clock.schedule(
                    session.leave_s,
                    lambda worker_id=sim_worker.worker_id: _safe_worker_leave(
                        pool,
                        worker_id,
                    ),
                )

    request_model_indices = _request_model_indices(len(models))
    traffic_windows = _traffic_windows()
    for window in traffic_windows:
        pool.log(
            "traffic_window_registered",
            label=window.label,
            start_s=window.start_s,
            end_s=window.end_s,
            demand_multiplier=window.demand_multiplier,
        )
    _register_incident_windows(pool, incidents)
    for request_id in range(n_turns):
        request_s = _request_time_s(request_id, n_turns, traffic_windows)
        person = _request_person(request_id, request_s, people, sessions_by_user)
        model_index = request_model_indices[
            (request_id * _persona_model_stride(person.persona))
            % len(request_model_indices)
        ]
        sim_model = models[model_index]
        traffic_window = _traffic_window_at(request_s, traffic_windows)
        override_model_id = _incident_model_override(
            request_id,
            request_s,
            traffic_window,
            artifacts_by_model,
        )
        if override_model_id is not None:
            sim_model = pool.models[override_model_id]
        input_tokens, output_tokens = _request_tokens(
            request_id,
            person.persona,
            traffic_window.demand_multiplier,
        )
        pool.clock.schedule(
            request_s,
            lambda request_id=request_id, user_id=person.user_id, model_id=sim_model.model_id, input_tokens=input_tokens, output_tokens=output_tokens: (
                _handle_request(
                    pool,
                    request_id=request_id,
                    user_id=user_id,
                    model_id=model_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    artifacts_by_model=artifacts_by_model,
                    attempt=0,
                )
            ),
        )

    for sample_s in _metric_sample_times(traffic_windows):
        pool.clock.schedule(
            sample_s,
            lambda sample_s=sample_s: pool.sample_metrics(f"t={int(sample_s)}s"),
        )
    for day in range(1, 7):
        reap_s = day * SECONDS_PER_DAY + 4.0 * SECONDS_PER_HOUR
        pool.clock.schedule(
            reap_s,
            lambda: _reap_pool_caches(pool, free_disk_fraction=0.12),
        )
    _schedule_week_incidents(pool, incidents, incident_context)

    pool.clock.run_until_idle(max_events=max(1_000_000, n_turns * 8))
    pool.sample_metrics("finished")
    return pool


def _locations() -> tuple[SimLocation, ...]:
    return (
        SimLocation("stanford-campus", "us-west", "bay-area", "campus", 14.0),
        SimLocation("berkeley-campus", "us-west", "bay-area", "campus", 16.0),
        SimLocation("sf-home", "us-west", "bay-area", "home-fiber", 20.0),
        SimLocation("seattle-home", "us-west", "pacific-nw", "home-cable", 38.0),
        SimLocation("nyc-office", "us-east", "nyc", "office", 63.0),
        SimLocation("rural-montana", "us-west", "rural", "home-dsl", 88.0),
        SimLocation("london-flat", "eu-west", "london", "home-fiber", 126.0),
        SimLocation("berlin-lab", "eu-central", "berlin", "university", 142.0),
        SimLocation("tokyo-apartment", "apac", "tokyo", "home-fiber", 168.0),
        SimLocation("sydney-home", "apac", "sydney", "home-cable", 196.0),
        SimLocation("cloud-us-west", "us-west", "cloud-west", "datacenter", 5.0),
        SimLocation("cloud-us-east", "us-east", "cloud-east", "datacenter", 58.0),
    )


def _people(
    n_users: int,
    locations: tuple[SimLocation, ...],
) -> list[SimPersonProfile]:
    people: list[SimPersonProfile] = []
    user_locations = locations[:10]
    for index in range(n_users):
        location = user_locations[(index * 7) % len(user_locations)]
        persona = _persona_for_index(index)
        machine_count = 0
        if index % 10 == 0:
            machine_count = 3
        elif index % 4 == 0:
            machine_count = 2
        elif index % 3 == 0:
            machine_count = 1
        people.append(
            SimPersonProfile(
                user_id=f"u{index}",
                account_id=f"acc-u{index}",
                persona=persona,
                location=location,
                machine_count=machine_count,
            )
        )
    return people


def _persona_for_index(index: int) -> Persona:
    personas: tuple[Persona, ...] = (
        "workday_builder",
        "evening_hobbyist",
        "workday_builder",
        "weekend_hobbyist",
        "research_lab",
        "evening_hobbyist",
        "batch_automation",
        "workday_builder",
        "evening_hobbyist",
        "research_lab",
    )
    return personas[index % len(personas)]


def _person_sessions(person: SimPersonProfile) -> tuple[SimSession, ...]:
    sessions: list[SimSession] = []
    offset = float((int(person.user_id[1:]) * 97) % 1_800)
    for day in range(7):
        day_start = day * SECONDS_PER_DAY
        if person.persona == "workday_builder":
            if day < 5:
                sessions.append(
                    _session(day_start + 8.0 * SECONDS_PER_HOUR + offset, 4.25)
                )
                sessions.append(
                    _session(day_start + 13.0 * SECONDS_PER_HOUR + offset / 2, 5.5)
                )
            elif day == 6 and int(person.user_id[1:]) % 5 == 0:
                sessions.append(_session(day_start + 11.0 * SECONDS_PER_HOUR, 2.0))
        elif person.persona == "evening_hobbyist":
            start_hour = 18.5 if day < 5 else 15.0
            duration = 5.0 if day < 5 else 8.0
            sessions.append(
                _session(day_start + start_hour * SECONDS_PER_HOUR, duration)
            )
        elif person.persona == "weekend_hobbyist":
            if day < 5:
                sessions.append(_session(day_start + 21.0 * SECONDS_PER_HOUR, 2.0))
            else:
                sessions.append(_session(day_start + 10.0 * SECONDS_PER_HOUR, 6.5))
                sessions.append(_session(day_start + 19.0 * SECONDS_PER_HOUR, 6.0))
        elif person.persona == "research_lab":
            if day < 6:
                sessions.append(_session(day_start + 9.0 * SECONDS_PER_HOUR, 11.5))
            if day in {1, 3, 5}:
                sessions.append(_session(day_start + 22.0 * SECONDS_PER_HOUR, 4.0))
        else:
            sessions.append(_session(day_start + 1.0 * SECONDS_PER_HOUR, 2.0))
            if day < 5:
                sessions.append(_session(day_start + 11.0 * SECONDS_PER_HOUR, 1.5))
                sessions.append(_session(day_start + 17.0 * SECONDS_PER_HOUR, 1.25))
    return tuple(
        session
        for session in sessions
        if 0.0 <= session.join_s < session.leave_s <= SIMULATION_HORIZON_S
    )


def _session(start_s: float, duration_hours: float) -> SimSession:
    return SimSession(
        join_s=max(0.0, start_s),
        leave_s=min(SIMULATION_HORIZON_S, start_s + duration_hours * SECONDS_PER_HOUR),
    )


def _workers_for_people(
    n_workers: int,
    people: list[SimPersonProfile],
    locations: tuple[SimLocation, ...],
) -> list[SimWorker]:
    workers: list[SimWorker] = []
    for person in people:
        for slot in range(person.machine_count):
            if len(workers) >= n_workers:
                return workers
            workers.append(_owned_worker(len(workers), person, slot))

    provider_locations = (locations[10], locations[11], locations[7], locations[4])
    if len(workers) < n_workers:
        workers.append(_extreme_provider_worker(len(workers), locations[10]))
    while len(workers) < n_workers:
        index = len(workers)
        location = provider_locations[index % len(provider_locations)]
        workers.append(_provider_worker(index, location))
    return workers


def _owned_worker(
    index: int,
    person: SimPersonProfile,
    slot: int,
) -> SimWorker:
    if slot == 0:
        ram_gb, disk_gb, compute, memory, read, write, download = (
            24.0 + float((index % 3) * 8),
            160.0 + float((index % 4) * 64),
            0.9 + float(index % 4) * 0.35,
            60.0 + float(index % 5) * 15.0,
            1.1,
            0.45,
            0.035 + float(index % 4) * 0.015,
        )
    elif slot == 1:
        ram_gb, disk_gb, compute, memory, read, write, download = (
            64.0 + float((index % 2) * 32),
            512.0,
            3.5 + float(index % 3),
            180.0 + float(index % 4) * 45.0,
            3.0,
            1.2,
            0.09 + float(index % 5) * 0.025,
        )
    else:
        ram_gb, disk_gb, compute, memory, read, write, download = (
            128.0,
            1_024.0,
            8.0 + float(index % 4),
            520.0 + float(index % 3) * 80.0,
            5.8,
            2.2,
            0.18 + float(index % 4) * 0.05,
        )
    return worker(
        f"w{index}",
        account_id=person.account_id,
        owner_user_id=person.user_id,
        location=person.location.name,
        ram_gb=ram_gb,
        disk_gb=disk_gb,
        download_gb_per_s=download,
        disk_read_gb_per_s=read,
        disk_write_gb_per_s=write,
        memory_bandwidth_gb_per_s=memory,
        compute_tflops=compute,
        hub_rtt_ms=person.location.hub_rtt_ms,
    )


def _extreme_provider_worker(index: int, location: SimLocation) -> SimWorker:
    return worker(
        f"w{index}",
        account_id=EXTREME_PROVIDER_ACCOUNT,
        owner_user_id=None,
        location=location.name,
        ram_gb=4_096.0,
        disk_gb=12_000.0,
        download_gb_per_s=5.0,
        disk_read_gb_per_s=40.0,
        disk_write_gb_per_s=20.0,
        memory_bandwidth_gb_per_s=8_000.0,
        compute_tflops=250.0,
        hub_rtt_ms=location.hub_rtt_ms,
    )


def _provider_worker(index: int, location: SimLocation) -> SimWorker:
    provider_offset = index % 24
    if provider_offset in {0, 11}:
        ram_gb, disk_gb, compute, memory, read, write, download = (
            768.0,
            1_800.0,
            34.0,
            1_500.0,
            11.0,
            5.0,
            1.2,
        )
    elif provider_offset in {3, 7, 15, 19}:
        ram_gb, disk_gb, compute, memory, read, write, download = (
            256.0,
            1_200.0,
            18.0,
            850.0,
            7.0,
            3.2,
            0.65,
        )
    else:
        ram_gb, disk_gb, compute, memory, read, write, download = (
            96.0 + float((index % 3) * 32),
            640.0,
            7.0 + float(index % 5),
            360.0 + float(index % 6) * 45.0,
            4.0,
            1.8,
            0.22 + float(index % 5) * 0.04,
        )
    return worker(
        f"w{index}",
        account_id=f"acc-provider-{index % 17}",
        owner_user_id=None,
        location=location.name,
        ram_gb=ram_gb,
        disk_gb=disk_gb,
        download_gb_per_s=download,
        disk_read_gb_per_s=read,
        disk_write_gb_per_s=write,
        memory_bandwidth_gb_per_s=memory,
        compute_tflops=compute,
        hub_rtt_ms=location.hub_rtt_ms,
    )


def _worker_sessions(
    index: int,
    sim_worker: SimWorker,
    owner: SimPersonProfile | None,
) -> tuple[SimSession, ...]:
    if owner is None:
        return _provider_worker_sessions(index, sim_worker)
    owner_sessions = _person_sessions(owner)
    if index % 3 == 0:
        return tuple(
            _bounded_session(
                session.join_s - 0.5 * SECONDS_PER_HOUR,
                session.leave_s + 1.0 * SECONDS_PER_HOUR,
            )
            for session in owner_sessions
        )
    if index % 3 == 1:
        return tuple(
            _bounded_session(
                session.join_s + float((index % 5) * 120),
                session.leave_s + 0.25 * SECONDS_PER_HOUR,
            )
            for session in owner_sessions
        )
    sessions: list[SimSession] = []
    for day in range(7):
        day_start = day * SECONDS_PER_DAY
        sessions.append(_session(day_start + 7.0 * SECONDS_PER_HOUR, 16.0))
    return tuple(sessions)


def _provider_worker_sessions(
    index: int,
    sim_worker: SimWorker,
) -> tuple[SimSession, ...]:
    if sim_worker.account_id == EXTREME_PROVIDER_ACCOUNT:
        # The explicit high-capacity provider joins after the pool has
        # already warmed models, absorbs replicas during midweek overload,
        # then leaves before weekend peaks so the run exercises recovery.
        return (
            _bounded_session(
                2 * SECONDS_PER_DAY + 10.0 * SECONDS_PER_HOUR,
                4 * SECONDS_PER_DAY + 19.0 * SECONDS_PER_HOUR,
            ),
        )
    sessions: list[SimSession] = []
    if index % 11 == 0:
        for day in range(7):
            day_start = day * SECONDS_PER_DAY
            sessions.append(_session(day_start + 8.0 * SECONDS_PER_HOUR, 11.0))
        return tuple(sessions)
    maintenance_day = index % 7
    maintenance_start = (
        maintenance_day * SECONDS_PER_DAY + (2.0 + float(index % 4)) * SECONDS_PER_HOUR
    )
    sessions.append(_bounded_session(0.0, maintenance_start))
    sessions.append(
        _bounded_session(
            maintenance_start + (0.5 + float(index % 3) * 0.5) * SECONDS_PER_HOUR,
            SIMULATION_HORIZON_S,
        )
    )
    return tuple(session for session in sessions if session.join_s < session.leave_s)


def _bounded_session(start_s: float, leave_s: float) -> SimSession:
    return SimSession(
        join_s=max(0.0, start_s),
        leave_s=min(SIMULATION_HORIZON_S, leave_s),
    )


def _join_worker(
    pool: SimPool,
    sim_worker: SimWorker,
    index: int,
    models: list[SimModel],
) -> None:
    if sim_worker.online:
        return
    pool.worker_join(sim_worker)
    if index % 3 == 0:
        pool.seed_installed_backend(sim_worker.worker_id, "mlx")
    if index % 13 == 0:
        cached_model = models[(index // 13) % min(len(models), 4)]
        try:
            pool.seed_source_artifact(
                sim_worker.worker_id, cached_model.model_id, "mlx"
            )
        except Exception as exc:
            pool.log(
                "cache_seed_skipped",
                worker_id=sim_worker.worker_id,
                model_id=cached_model.model_id,
                reason=str(exc),
            )


def _safe_worker_leave(pool: SimPool, worker_id: str) -> None:
    worker = pool.workers.get(worker_id)
    if worker is not None and worker.online:
        pool.worker_leave(worker_id)


def _safe_user_join(pool: SimPool, sim_user: SimUser) -> None:
    if sim_user.user_id not in pool.users:
        pool.user_join(sim_user)


def _safe_user_leave(pool: SimPool, user_id: str) -> None:
    if user_id in pool.users:
        pool.user_leave(user_id)


def _reap_pool_caches(pool: SimPool, *, free_disk_fraction: float) -> None:
    for sim_worker in pool.workers.values():
        if not sim_worker.online:
            continue
        target_free_gb = sim_worker.disk_gb * free_disk_fraction
        if sim_worker.disk_available_gb < target_free_gb:
            pool.reap_cache(sim_worker.worker_id, required_free_gb=target_free_gb)
    pool.log("cache_reaper_ran", free_disk_fraction=free_disk_fraction)


def _week_incidents() -> tuple[SimIncident, ...]:
    return (
        SimIncident(
            "many_small_workers_huge_model",
            MONDAY + 11.0 * SECONDS_PER_HOUR,
            MONDAY + 11.25 * SECONDS_PER_HOUR,
            on_start=_incident_huge_model_small_workers,
        ),
        SimIncident(
            "network_partition",
            MONDAY + 16.0 * SECONDS_PER_HOUR,
            MONDAY + 18.0 * SECONDS_PER_HOUR,
            on_start=_incident_network_partition_start,
            on_finish=_incident_network_partition_finish,
        ),
        SimIncident(
            "slow_disk_death",
            TUESDAY + 7.0 * SECONDS_PER_HOUR,
            TUESDAY + 19.0 * SECONDS_PER_HOUR,
            on_start=_incident_slow_disk_start,
            on_finish=_incident_slow_disk_finish,
        ),
        SimIncident(
            "partial_cache_corruption",
            TUESDAY + 13.0 * SECONDS_PER_HOUR,
            TUESDAY + 13.25 * SECONDS_PER_HOUR,
            on_start=_incident_cache_corruption,
        ),
        SimIncident(
            "hot_model_no_free_ram",
            WEDNESDAY + 10.0 * SECONDS_PER_HOUR,
            WEDNESDAY + 12.0 * SECONDS_PER_HOUR,
            on_start=_incident_ram_pressure_start,
            on_finish=_incident_ram_pressure_finish,
        ),
        SimIncident(
            "asymmetric_network",
            WEDNESDAY + 14.0 * SECONDS_PER_HOUR,
            WEDNESDAY + 20.0 * SECONDS_PER_HOUR,
            on_start=_incident_asymmetric_network_start,
            on_finish=_incident_asymmetric_network_finish,
        ),
        SimIncident(
            "competing_model_priorities",
            THURSDAY + 9.0 * SECONDS_PER_HOUR,
            THURSDAY + 17.0 * SECONDS_PER_HOUR,
            on_start=_incident_log_start,
            on_finish=_incident_log_finish,
        ),
        SimIncident(
            "model_upgrade_swap",
            FRIDAY + 11.0 * SECONDS_PER_HOUR,
            FRIDAY + 18.0 * SECONDS_PER_HOUR,
            on_start=_incident_model_upgrade_start,
            on_finish=_incident_model_upgrade_finish,
        ),
        SimIncident(
            "whale_worker_churn",
            WEDNESDAY + 10.0 * SECONDS_PER_HOUR,
            FRIDAY + 19.0 * SECONDS_PER_HOUR,
            on_start=_incident_log_start,
            on_finish=_incident_log_finish,
        ),
        SimIncident(
            "medium_model_flash_crowd",
            TUESDAY + 10.0 * SECONDS_PER_HOUR,
            SUNDAY + 23.5 * SECONDS_PER_HOUR,
            on_start=_incident_log_start,
            on_finish=_incident_log_finish,
        ),
        SimIncident(
            "provider_fairness_concentration",
            SUNDAY + 23.0 * SECONDS_PER_HOUR,
            SIMULATION_HORIZON_S,
            on_finish=_incident_provider_fairness_report,
        ),
    )


def _register_incident_windows(
    pool: SimPool,
    incidents: tuple[SimIncident, ...],
) -> None:
    for incident in incidents:
        pool.log(
            "incident_window_registered",
            label=incident.label,
            start_s=incident.start_s,
            end_s=incident.end_s,
        )


def _schedule_week_incidents(
    pool: SimPool,
    incidents: tuple[SimIncident, ...],
    context: IncidentContext,
) -> None:
    for incident in incidents:
        if incident.on_start is not None:
            pool.clock.schedule(
                incident.start_s,
                lambda incident=incident: incident.on_start(
                    pool,
                    context,
                    incident.label,
                ),
            )
        if incident.on_finish is not None:
            pool.clock.schedule(
                incident.end_s,
                lambda incident=incident: incident.on_finish(
                    pool,
                    context,
                    incident.label,
                ),
            )


def _incident_model_override(
    request_id: int,
    request_s: float,
    traffic_window: TrafficWindow,
    artifacts_by_model: dict[str, BackendArtifactProfile],
) -> str | None:
    if (
        _in_window(
            request_s,
            FRIDAY + 11.0 * SECONDS_PER_HOUR,
            FRIDAY + 18.0 * SECONDS_PER_HOUR,
        )
        and UPGRADE_TARGET_MODEL_ID in artifacts_by_model
        and request_id % 2 == 0
    ):
        return UPGRADE_TARGET_MODEL_ID
    if _in_window(
        request_s,
        THURSDAY + 9.0 * SECONDS_PER_HOUR,
        THURSDAY + 17.0 * SECONDS_PER_HOUR,
    ):
        if request_id % 5 == 0 and "mlx-community/Kimi-K2.5" in artifacts_by_model:
            return "mlx-community/Kimi-K2.5"
        if (
            request_id % 5 == 1
            and "mlx-community/Qwen3.6-35B-A3B-8bit" in artifacts_by_model
        ):
            return "mlx-community/Qwen3.6-35B-A3B-8bit"
        if request_id % 5 in {2, 3} and FLASH_CROWD_MODEL_ID in artifacts_by_model:
            return FLASH_CROWD_MODEL_ID
    if (
        _in_window(
            request_s,
            WEDNESDAY + 10.0 * SECONDS_PER_HOUR,
            WEDNESDAY + 12.0 * SECONDS_PER_HOUR,
        )
        and FLASH_CROWD_MODEL_ID in artifacts_by_model
        and request_id % 2 == 0
    ):
        return FLASH_CROWD_MODEL_ID
    if (
        traffic_window.demand_multiplier >= OVERLOAD_DEMAND_MULTIPLIER
        and FLASH_CROWD_MODEL_ID in artifacts_by_model
        and request_id % 3 != 0
    ):
        return FLASH_CROWD_MODEL_ID
    return None


def _in_window(time_s: float, start_s: float, end_s: float) -> bool:
    return start_s <= time_s < end_s


def _incident_log_start(
    pool: SimPool,
    _context: IncidentContext,
    label: str,
) -> None:
    pool.log(f"incident_{label}_started")


def _incident_log_finish(
    pool: SimPool,
    _context: IncidentContext,
    label: str,
) -> None:
    pool.log(f"incident_{label}_finished")


def _incident_model_upgrade_start(
    pool: SimPool,
    _context: IncidentContext,
    _label: str,
) -> None:
    pool.log(
        "incident_model_upgrade_started",
        source_model_id=FLASH_CROWD_MODEL_ID,
        target_model_id=UPGRADE_TARGET_MODEL_ID,
    )


def _incident_model_upgrade_finish(
    pool: SimPool,
    _context: IncidentContext,
    _label: str,
) -> None:
    pool.log(
        "incident_model_upgrade_finished",
        source_model_id=FLASH_CROWD_MODEL_ID,
        target_model_id=UPGRADE_TARGET_MODEL_ID,
    )


def _incident_huge_model_small_workers(
    pool: SimPool,
    context: IncidentContext,
    _label: str,
) -> None:
    artifact = context.artifacts_by_model.get("mlx-community/Kimi-K2.5")
    if artifact is None:
        return
    small_online = [
        sim_worker
        for sim_worker in pool.workers.values()
        if sim_worker.online
        and (
            sim_worker.ram_available_gb < artifact.resident_ram_gb
            or sim_worker.disk_available_gb < artifact.source_gb
        )
    ]
    pool.log(
        "incident_huge_model_small_workers_rejected",
        model_id=artifact.model_id,
        rejected_workers=len(small_online),
        required_ram_gb=artifact.resident_ram_gb,
        required_disk_gb=artifact.source_gb,
    )


def _incident_network_partition_start(
    pool: SimPool,
    context: IncidentContext,
    label: str,
) -> None:
    location = "berlin-lab"
    affected = [
        sim_worker
        for sim_worker in pool.workers.values()
        if sim_worker.online and sim_worker.location == location
    ]
    pool.log(
        "incident_network_partition_started",
        location=location,
        affected_workers=len(affected),
    )
    affected_ids = [sim_worker.worker_id for sim_worker in affected]
    for sim_worker in affected:
        pool.worker_leave(sim_worker.worker_id)
    context.state[f"{label}:affected_ids"] = affected_ids


def _incident_network_partition_finish(
    pool: SimPool,
    context: IncidentContext,
    label: str,
) -> None:
    affected_ids = context.state.get(f"{label}:affected_ids", [])
    rejoined = 0
    for worker_id in affected_ids if isinstance(affected_ids, list) else []:
        sim_worker = pool.workers.get(str(worker_id))
        if sim_worker is None or sim_worker.online:
            continue
        _join_worker(
            pool,
            sim_worker,
            context.worker_indices.get(sim_worker.worker_id, 0),
            context.models,
        )
        rejoined += 1
    pool.log(
        "incident_network_partition_finished",
        location="berlin-lab",
        rejoined_workers=rejoined,
    )


def _incident_slow_disk_start(
    pool: SimPool,
    context: IncidentContext,
    label: str,
) -> None:
    candidates = [
        sim_worker
        for sim_worker in pool.workers.values()
        if sim_worker.online
        and sim_worker.owner_user_id is None
        and sim_worker.account_id != EXTREME_PROVIDER_ACCOUNT
    ]
    if not candidates:
        pool.log("incident_slow_disk_started", affected_workers=0)
        context.state[f"{label}:original"] = {}
        return
    affected = sorted(candidates, key=lambda worker: worker.disk_read_gb_per_s)[-3:]
    original = {
        worker.worker_id: (
            worker.download_gb_per_s,
            worker.disk_read_gb_per_s,
            worker.disk_write_gb_per_s,
        )
        for worker in affected
    }
    for worker in affected:
        worker.download_gb_per_s *= 0.12
        worker.disk_read_gb_per_s *= 0.08
        worker.disk_write_gb_per_s *= 0.08
    context.state[f"{label}:original"] = original
    pool.log(
        "incident_slow_disk_started",
        affected_workers=len(affected),
        worker_ids=[worker.worker_id for worker in affected],
    )


def _incident_slow_disk_finish(
    pool: SimPool,
    context: IncidentContext,
    label: str,
) -> None:
    original = context.state.get(f"{label}:original", {})
    restored = 0
    if isinstance(original, dict):
        for worker_id, speeds in original.items():
            worker = pool.workers.get(str(worker_id))
            if worker is None:
                continue
            (
                worker.download_gb_per_s,
                worker.disk_read_gb_per_s,
                worker.disk_write_gb_per_s,
            ) = speeds
            restored += 1
    pool.log("incident_slow_disk_finished", restored_workers=restored)


def _incident_cache_corruption(
    pool: SimPool,
    context: IncidentContext,
    _label: str,
) -> None:
    model_id = FLASH_CROWD_MODEL_ID
    artifact = context.artifacts_by_model.get(model_id)
    if artifact is None:
        return
    key = (model_id, artifact.backend)
    candidates = [
        sim_worker
        for sim_worker in pool.workers.values()
        if sim_worker.online
        and (key in sim_worker.source_artifacts or key in sim_worker.prepared_artifacts)
    ]
    if not candidates:
        pool.log("incident_cache_corruption_skipped", model_id=model_id)
        return
    victim = sorted(candidates, key=lambda worker: worker.worker_id)[0]
    released_gb = 0.0
    if key in victim.source_artifacts:
        victim.source_artifacts.remove(key)
        released_gb += artifact.source_gb
    if key in victim.prepared_artifacts:
        victim.prepared_artifacts.remove(key)
        if not artifact.source_is_prepared:
            released_gb += artifact.prepared_gb
    victim.artifact_last_used_s.pop(key, None)
    victim.disk_reserved_gb = max(0.0, victim.disk_reserved_gb - released_gb)
    pool.log(
        "incident_cache_corrupted",
        worker_id=victim.worker_id,
        model_id=model_id,
        released_gb=released_gb,
    )


def _incident_ram_pressure_start(
    pool: SimPool,
    context: IncidentContext,
    label: str,
) -> None:
    affected: dict[str, float] = {}
    for sim_worker in pool.workers.values():
        if (
            not sim_worker.online
            or sim_worker.owner_user_id is None
            or sim_worker.ram_gb > 160.0
        ):
            continue
        pressure_gb = sim_worker.ram_available_gb * 0.75
        if pressure_gb <= 0:
            continue
        sim_worker.ram_reserved_gb += pressure_gb
        affected[sim_worker.worker_id] = pressure_gb
    context.state[f"{label}:affected"] = affected
    pool.log(
        "incident_hot_model_no_free_ram_started",
        affected_workers=len(affected),
        reserved_ram_gb=sum(affected.values()),
        model_id=FLASH_CROWD_MODEL_ID,
    )


def _incident_ram_pressure_finish(
    pool: SimPool,
    context: IncidentContext,
    label: str,
) -> None:
    affected = context.state.get(f"{label}:affected", {})
    released = 0.0
    if isinstance(affected, dict):
        for worker_id, amount_gb in affected.items():
            worker = pool.workers.get(str(worker_id))
            if worker is None:
                continue
            worker.ram_reserved_gb = max(0.0, worker.ram_reserved_gb - float(amount_gb))
            released += float(amount_gb)
    pool.log(
        "incident_hot_model_no_free_ram_finished",
        affected_workers=len(affected) if isinstance(affected, dict) else 0,
        released_ram_gb=released,
        model_id=FLASH_CROWD_MODEL_ID,
    )


def _incident_asymmetric_network_start(
    pool: SimPool,
    context: IncidentContext,
    label: str,
) -> None:
    online = [worker for worker in pool.workers.values() if worker.online]
    if len(online) < 2:
        pool.log("incident_asymmetric_network_started", affected_links=0)
        context.state[f"{label}:links"] = None
        return
    left = min(online, key=lambda worker: worker.worker_id)
    right = max(online, key=lambda worker: worker.worker_id)
    old_left = (
        left.peer_rtt_ms[right.worker_id],
        left.peer_bandwidth_gb_per_s[right.worker_id],
    )
    old_right = (
        right.peer_rtt_ms[left.worker_id],
        right.peer_bandwidth_gb_per_s[left.worker_id],
    )
    left.peer_rtt_ms[right.worker_id] = 280.0
    left.peer_bandwidth_gb_per_s[right.worker_id] = 0.05
    right.peer_rtt_ms[left.worker_id] = 8.0
    right.peer_bandwidth_gb_per_s[left.worker_id] = 10.0
    context.state[f"{label}:links"] = (
        left.worker_id,
        right.worker_id,
        old_left,
        old_right,
    )
    pool.log(
        "incident_asymmetric_network_started",
        left_worker_id=left.worker_id,
        right_worker_id=right.worker_id,
    )


def _incident_asymmetric_network_finish(
    pool: SimPool,
    context: IncidentContext,
    label: str,
) -> None:
    links = context.state.get(f"{label}:links")
    if not isinstance(links, tuple):
        pool.log("incident_asymmetric_network_finished", affected_links=0)
        return
    left_id, right_id, old_left, old_right = links
    left = pool.workers.get(str(left_id))
    right = pool.workers.get(str(right_id))
    if left is not None and right is not None:
        if right.worker_id in left.peer_rtt_ms:
            left.peer_rtt_ms[right.worker_id] = old_left[0]
            left.peer_bandwidth_gb_per_s[right.worker_id] = old_left[1]
        if left.worker_id in right.peer_rtt_ms:
            right.peer_rtt_ms[left.worker_id] = old_right[0]
            right.peer_bandwidth_gb_per_s[left.worker_id] = old_right[1]
    pool.log(
        "incident_asymmetric_network_finished",
        left_worker_id=left_id,
        right_worker_id=right_id,
    )


def _incident_provider_fairness_report(
    pool: SimPool,
    _context: IncidentContext,
    _label: str,
) -> None:
    balances = [
        balance for balance in pool.metrics().ledger_balances.values() if balance > 0
    ]
    total_earned = sum(balances)
    top_earned = max(balances) if balances else 0.0
    pool.log(
        "incident_provider_fairness_reported",
        provider_count=len(balances),
        top_provider_share=(top_earned / total_earned if total_earned else 0.0),
        total_provider_cu=total_earned,
    )


def _request_model_indices(n_models: int) -> tuple[int, ...]:
    weights = (18, 15, 13, 12, 9, 6, 4, 9, 5, 5, 4, 2)
    indices: list[int] = []
    for index in range(n_models):
        indices.extend([index] * weights[index % len(weights)])
    return tuple(indices)


def _traffic_windows() -> tuple[TrafficWindow, ...]:
    windows: list[TrafficWindow] = []
    for day in range(7):
        if day < 5:
            windows.extend(
                (
                    _traffic_window(day, "weekday_quiet_night", 0.0, 7.0, 0.0),
                    _traffic_window(day, "weekday_ramp", 7.0, 9.0, 0.6),
                    _traffic_window(day, "weekday_morning", 9.0, 10.0, 1.4),
                    _traffic_window(
                        day,
                        "weekday_overload",
                        10.0,
                        11.0,
                        OVERLOAD_DEMAND_MULTIPLIER if day in {1, 3} else 1.4,
                    ),
                    _traffic_window(day, "weekday_deep_work", 11.0, 12.0, 1.3),
                    _traffic_window(day, "weekday_lunch", 12.0, 14.0, 0.7),
                    _traffic_window(day, "weekday_afternoon", 14.0, 17.0, 1.5),
                    _traffic_window(
                        day,
                        "release_overload",
                        17.0,
                        19.0,
                        OVERLOAD_DEMAND_MULTIPLIER if day == 4 else 1.0,
                    ),
                    _traffic_window(day, "hobby_evening", 19.0, 23.0, 2.2),
                    _traffic_window(day, "weekday_quiet_late", 23.0, 24.0, 0.0),
                )
            )
        else:
            windows.extend(
                (
                    _traffic_window(day, "weekend_quiet_morning", 0.0, 9.0, 0.0),
                    _traffic_window(day, "weekend_hobby_start", 9.0, 12.0, 1.4),
                    _traffic_window(day, "weekend_hobby_midday", 12.0, 17.0, 2.4),
                    _traffic_window(
                        day,
                        "weekend_hobby_overload",
                        17.0,
                        23.5,
                        OVERLOAD_DEMAND_MULTIPLIER,
                    ),
                    _traffic_window(day, "weekend_quiet_late", 23.5, 24.0, 0.0),
                )
            )
    return tuple(windows)


def _traffic_window(
    day: int,
    label: str,
    start_hour: float,
    end_hour: float,
    demand_multiplier: float,
) -> TrafficWindow:
    day_start = day * SECONDS_PER_DAY
    return TrafficWindow(
        label=f"d{day}_{label}",
        start_s=day_start + start_hour * SECONDS_PER_HOUR,
        end_s=day_start + end_hour * SECONDS_PER_HOUR,
        demand_multiplier=demand_multiplier,
    )


def _request_time_s(
    request_id: int,
    n_turns: int,
    traffic_windows: tuple[TrafficWindow, ...],
) -> float:
    positive = [window for window in traffic_windows if window.demand_multiplier > 0]
    total_weight = sum(
        (window.end_s - window.start_s) * window.demand_multiplier
        for window in positive
    )
    target = (float(request_id) + 0.5) * total_weight / max(n_turns, 1)
    seen = 0.0
    for window in positive:
        window_weight = (window.end_s - window.start_s) * window.demand_multiplier
        if target <= seen + window_weight:
            offset_s = (target - seen) / window.demand_multiplier
            jitter_s = float((request_id * 17) % 29) * 0.37
            return min(window.end_s - 0.001, window.start_s + offset_s + jitter_s)
        seen += window_weight
    return positive[-1].end_s - 0.001


def _traffic_window_at(
    time_s: float,
    traffic_windows: tuple[TrafficWindow, ...],
) -> TrafficWindow:
    for window in traffic_windows:
        if window.start_s <= time_s < window.end_s:
            return window
    return TrafficWindow("outside_horizon", time_s, time_s, 0.0)


def _request_person(
    request_id: int,
    request_s: float,
    people: list[SimPersonProfile],
    sessions_by_user: dict[str, tuple[SimSession, ...]],
) -> SimPersonProfile:
    active_personas = _active_personas_for_time(request_s)
    candidates = [
        person
        for person in people
        if person.persona in active_personas
        and _person_online_at(sessions_by_user[person.user_id], request_s)
    ]
    if not candidates:
        candidates = [
            person
            for person in people
            if _person_online_at(sessions_by_user[person.user_id], request_s)
        ]
    if not candidates:
        candidates = people
    return candidates[(request_id * 37) % len(candidates)]


def _person_online_at(sessions: tuple[SimSession, ...], time_s: float) -> bool:
    return any(session.join_s <= time_s < session.leave_s for session in sessions)


def _active_personas_for_time(time_s: float) -> frozenset[Persona]:
    day = int(time_s // SECONDS_PER_DAY)
    hour = (time_s - day * SECONDS_PER_DAY) / SECONDS_PER_HOUR
    if day >= 5:
        if 9.0 <= hour < 17.0:
            return frozenset({"weekend_hobbyist", "evening_hobbyist", "research_lab"})
        if 17.0 <= hour < 23.5:
            return frozenset(
                {"weekend_hobbyist", "evening_hobbyist", "batch_automation"}
            )
    if 7.0 <= hour < 12.0:
        return frozenset({"workday_builder", "research_lab", "batch_automation"})
    if 12.0 <= hour < 17.0:
        return frozenset({"workday_builder", "research_lab"})
    if 17.0 <= hour < 23.0:
        return frozenset({"evening_hobbyist", "weekend_hobbyist", "workday_builder"})
    return frozenset({"batch_automation", "research_lab"})


def _persona_model_stride(persona: Persona) -> int:
    return {
        "workday_builder": 19,
        "evening_hobbyist": 23,
        "weekend_hobbyist": 29,
        "research_lab": 31,
        "batch_automation": 37,
    }[persona]


def _request_tokens(
    request_id: int,
    persona: Persona,
    demand_multiplier: float,
) -> tuple[int, int]:
    input_tokens = 8 + (request_id * 11) % 256
    base_output = 24 + (request_id * 7) % 240
    if persona == "batch_automation":
        base_output += 160
    elif persona == "research_lab":
        base_output += 96
    elif persona == "weekend_hobbyist":
        base_output += 48
    if demand_multiplier >= OVERLOAD_DEMAND_MULTIPLIER:
        base_output = int(base_output * OVERLOAD_OUTPUT_TOKEN_MULTIPLIER)
    return input_tokens, base_output


def _metric_sample_times(
    traffic_windows: tuple[TrafficWindow, ...],
) -> tuple[float, ...]:
    sample_times = {0.0, SIMULATION_HORIZON_S}
    for day in range(7):
        day_start = day * SECONDS_PER_DAY
        for hour in (3.0, 10.5, 15.0, 18.0, 21.0):
            sample_times.add(day_start + hour * SECONDS_PER_HOUR)
    for window in traffic_windows:
        if window.demand_multiplier >= OVERLOAD_DEMAND_MULTIPLIER:
            sample_times.add(window.start_s + (window.end_s - window.start_s) / 2)
    return tuple(
        sorted(time_s for time_s in sample_times if time_s <= SIMULATION_HORIZON_S)
    )


def _handle_request(
    pool: SimPool,
    *,
    request_id: int,
    user_id: str,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    artifacts_by_model: dict[str, BackendArtifactProfile],
    attempt: int,
) -> None:
    first_attempt = attempt == 0
    if first_attempt:
        pool.log(
            "request_received",
            request_id=request_id,
            user_id=user_id,
            model_id=model_id,
        )
    if user_id not in pool.users:
        pool.log(
            "request_skipped_user_offline",
            request_id=request_id,
            user_id=user_id,
            model_id=model_id,
            attempt=attempt,
        )
        return

    user_account = pool.users[user_id].account_id
    ready_workers = _ready_workers_for_model(pool, model_id)
    if ready_workers:
        model = pool.models[model_id]
        _maybe_start_pressure_replica(
            pool,
            model_id=model_id,
            artifact=artifacts_by_model[model_id],
            user_account=user_account,
            ready_workers=ready_workers,
        )
        if model.active_params >= 30_000_000_000 and len(ready_workers) >= 2:
            shard_workers = _choose_pipeline_workers(
                pool,
                ready_workers,
                model_id,
                user_id,
            )
            turn = pool.start_sharded_turn(
                user_id,
                model_id,
                worker_ids=tuple(worker.worker_id for worker in shard_workers),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            pool.log(
                "request_served",
                request_id=request_id,
                user_id=user_id,
                model_id=model_id,
                worker_ids=turn.worker_ids,
                decision="pipeline_reuse",
                tps=turn.tps,
                attempt=attempt,
            )
            return
        chosen = _choose_serving_worker(pool, ready_workers, model_id, user_id)
        turn = pool.start_turn(
            user_id,
            model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            worker_id=chosen.worker_id,
        )
        pool.log(
            "request_served",
            request_id=request_id,
            user_id=user_id,
            model_id=model_id,
            worker_id=chosen.worker_id,
            decision="reuse_loaded",
            tps=turn.tps,
            attempt=attempt,
        )
        return

    if attempt >= MAX_REQUEST_ATTEMPTS:
        pool.log(
            "request_abandoned",
            request_id=request_id,
            user_id=user_id,
            model_id=model_id,
            attempt=attempt,
        )
        return

    active_load = _active_load_for_model(pool, model_id)
    if active_load is not None:
        retry_delay = max(
            REQUEST_RETRY_S,
            _load_ready_estimate_s(active_load) - pool.clock.now_s + 1.0,
        )
        pool.log(
            "request_waiting_for_load",
            request_id=request_id,
            user_id=user_id,
            model_id=model_id,
            load_id=active_load.load_id,
            attempt=attempt,
            retry_delay_s=retry_delay,
        )
        pool.clock.schedule(
            retry_delay,
            lambda: _handle_request(
                pool,
                request_id=request_id,
                user_id=user_id,
                model_id=model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                artifacts_by_model=artifacts_by_model,
                attempt=attempt + 1,
            ),
        )
        return

    artifact = artifacts_by_model[model_id]
    load_worker = _choose_load_worker(pool, artifact, user_account)
    if load_worker is None:
        retry_delay = REQUEST_RETRY_S * (1.0 + float(attempt % 4))
        pool.log(
            "request_waiting_for_capacity",
            request_id=request_id,
            user_id=user_id,
            model_id=model_id,
            attempt=attempt,
            retry_delay_s=retry_delay,
        )
        pool.clock.schedule(
            retry_delay,
            lambda: _handle_request(
                pool,
                request_id=request_id,
                user_id=user_id,
                model_id=model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                artifacts_by_model=artifacts_by_model,
                attempt=attempt + 1,
            ),
        )
        return

    load = pool.start_load(load_worker.worker_id, model_id, backend=artifact.backend)
    secondary_load = None
    model = pool.models[model_id]
    if model.active_params >= 30_000_000_000:
        secondary_worker = _choose_load_worker(
            pool,
            artifact,
            user_account,
            exclude_worker_ids={load_worker.worker_id},
        )
        if secondary_worker is not None:
            secondary_load = pool.start_load(
                secondary_worker.worker_id,
                model_id,
                backend=artifact.backend,
            )
    retry_delay = max(
        REQUEST_RETRY_S, _load_ready_estimate_s(load) - pool.clock.now_s + 1.0
    )
    pool.log(
        "request_cold_load_started",
        request_id=request_id,
        user_id=user_id,
        model_id=model_id,
        worker_id=load_worker.worker_id,
        load_id=load.load_id,
        secondary_load_id=secondary_load.load_id if secondary_load else None,
        attempt=attempt,
        retry_delay_s=retry_delay,
    )
    pool.clock.schedule(
        retry_delay,
        lambda: _handle_request(
            pool,
            request_id=request_id,
            user_id=user_id,
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            artifacts_by_model=artifacts_by_model,
            attempt=attempt + 1,
        ),
    )


def _ready_workers_for_model(pool: SimPool, model_id: str) -> list[SimWorker]:
    return [
        worker
        for worker in pool.workers.values()
        if worker.online and model_id in worker.loaded_models
    ]


def _choose_serving_worker(
    pool: SimPool,
    workers: list[SimWorker],
    model_id: str,
    user_id: str,
) -> SimWorker:
    user_account = pool.users[user_id].account_id
    choices = [
        ServingWorkerChoice(
            worker_id=worker.worker_id,
            account_id=worker.account_id,
            active_turns=worker.active_turns,
            tps=pool.effective_tps(
                worker.worker_id,
                model_id,
                user_id=user_id,
                reserve_turn_slot=True,
            ),
        )
        for worker in workers
    ]
    chosen_id = policy_choose_serving_worker(
        choices,
        querier_account=user_account,
    )
    if chosen_id is None:
        raise RuntimeError("ready worker list was empty")
    return pool.workers[chosen_id]


def _choose_pipeline_workers(
    pool: SimPool,
    workers: list[SimWorker],
    model_id: str,
    user_id: str,
) -> tuple[SimWorker, SimWorker]:
    ordered = sorted(
        workers,
        key=lambda worker: pool.effective_tps(
            worker.worker_id,
            model_id,
            user_id=user_id,
            reserve_turn_slot=True,
        ),
        reverse=True,
    )
    best_pair = (ordered[0], ordered[1])
    best_tps = -1.0
    for left in ordered[:4]:
        for right in ordered[:4]:
            if left.worker_id == right.worker_id:
                continue
            tps = pool.sharded_effective_tps(
                (left.worker_id, right.worker_id),
                model_id,
                user_id=user_id,
                reserve_turn_slot=True,
            )
            if tps > best_tps:
                best_tps = tps
                best_pair = (left, right)
    return best_pair


def _active_load_for_model(pool: SimPool, model_id: str):
    candidates = [
        load
        for load in pool.loads.values()
        if load.model_id == model_id
        and not load.canceled
        and load.completed_at_s is None
    ]
    if not candidates:
        return None
    return min(candidates, key=_load_ready_estimate_s)


def _maybe_start_pressure_replica(
    pool: SimPool,
    *,
    model_id: str,
    artifact: BackendArtifactProfile,
    user_account: str,
    ready_workers: list[SimWorker],
) -> None:
    active_turns = sum(worker.active_turns for worker in ready_workers)
    if not should_start_pressure_replica(
        active_load_exists=_active_load_for_model(pool, model_id) is not None,
        ready_replica_count=len(ready_workers),
        ready_active_turns=active_turns,
        resident_ram_gb=artifact.resident_ram_gb,
    ):
        return
    load_worker = _choose_load_worker(
        pool,
        artifact,
        user_account,
        exclude_worker_ids={worker.worker_id for worker in ready_workers},
    )
    if load_worker is None:
        return
    load = pool.start_load(load_worker.worker_id, model_id, backend=artifact.backend)
    pool.log(
        "pressure_replica_started",
        model_id=model_id,
        worker_id=load_worker.worker_id,
        load_id=load.load_id,
        ready_replicas=len(ready_workers),
        active_turns=active_turns,
    )


def _replica_floor_for_artifact(artifact: BackendArtifactProfile) -> int:
    return replica_floor_for_resident_gb(artifact.resident_ram_gb)


def _replica_limit_for_artifact(artifact: BackendArtifactProfile) -> int:
    return replica_limit_for_resident_gb(artifact.resident_ram_gb)


def _load_ready_estimate_s(load) -> float:
    return load.started_at_s + sum(phase.duration_s for phase in load.phases)


def _choose_load_worker(
    pool: SimPool,
    artifact: BackendArtifactProfile,
    user_account: str,
    exclude_worker_ids: set[str] | None = None,
) -> SimWorker | None:
    snapshots = []
    for sim_worker in pool.workers.values():
        if not sim_worker.online:
            continue
        key = (artifact.model_id, artifact.backend)
        snapshots.append(
            ServingWorkerSnapshot(
                worker_id=sim_worker.worker_id,
                account_id=sim_worker.account_id,
                ram_available_gb=sim_worker.ram_available_gb,
                ram_reserved_gb=sim_worker.ram_reserved_gb,
                disk_available_gb=sim_worker.disk_available_gb,
                evictable_cache_gb=pool.evictable_cache_gb(sim_worker.worker_id),
                compute_tflops=sim_worker.compute_tflops,
                memory_bandwidth_gb_per_s=sim_worker.memory_bandwidth_gb_per_s,
                download_gb_per_s=sim_worker.download_gb_per_s,
                disk_read_gb_per_s=sim_worker.disk_read_gb_per_s,
                hub_rtt_ms=sim_worker.hub_rtt_ms,
                supports_backend=artifact.backend in sim_worker.backends,
                has_active_load=bool(sim_worker.active_loads),
                has_loaded_model=(
                    sim_worker.loaded_models.get(artifact.model_id) == artifact.backend
                ),
                has_source_artifact=key in sim_worker.source_artifacts,
                has_prepared_artifact=key in sim_worker.prepared_artifacts,
            )
        )
    chosen_id = policy_choose_load_worker(
        snapshots,
        artifact,
        user_account=user_account,
        exclude_worker_ids=exclude_worker_ids,
    )
    if chosen_id is None:
        return None
    return pool.workers[chosen_id]


def _connect_peer_mesh(
    workers: list[SimWorker],
    locations_by_name: dict[str, SimLocation],
) -> None:
    for left_index, left in enumerate(workers):
        for right_index, right in enumerate(workers):
            if left.worker_id == right.worker_id:
                continue
            left_location = locations_by_name[left.location]
            right_location = locations_by_name[right.location]
            left.peer_rtt_ms[right.worker_id] = _peer_rtt_ms(
                left_location,
                right_location,
                left_index,
                right_index,
            )
            left.peer_bandwidth_gb_per_s[right.worker_id] = _peer_bandwidth_gbps(
                left_location,
                right_location,
                left_index,
                right_index,
            )


def _peer_rtt_ms(
    left: SimLocation,
    right: SimLocation,
    left_index: int,
    right_index: int,
) -> float:
    if left.name == right.name:
        base = 0.8 if left.access in {"campus", "datacenter"} else 3.5
    elif left.metro == right.metro:
        base = 5.0 if "campus" in {left.access, right.access} else 8.0
    elif left.region == right.region:
        base = 24.0
    elif {left.region, right.region} <= {"us-west", "us-east"}:
        base = 63.0
    elif "apac" in {left.region, right.region}:
        base = 150.0
    elif "eu-west" in {left.region, right.region} or "eu-central" in {
        left.region,
        right.region,
    }:
        base = 112.0
    else:
        base = 85.0
    low, high = sorted((left_index, right_index))
    jitter = float((low * 31 + high * 17) % 9) * 0.6
    return base + jitter


def _peer_bandwidth_gbps(
    left: SimLocation,
    right: SimLocation,
    left_index: int,
    right_index: int,
) -> float:
    if left.name == right.name and left.access == "datacenter":
        base = 40.0
    elif left.name == right.name:
        base = 12.0
    elif left.metro == right.metro:
        base = 6.0
    elif left.region == right.region:
        base = 1.5
    else:
        base = 0.35
    low, high = sorted((left_index, right_index))
    return max(0.05, base - float((low + high) % 5) * base * 0.08)

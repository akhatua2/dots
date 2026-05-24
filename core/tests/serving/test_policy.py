from __future__ import annotations

from dots.backends.artifacts import BackendArtifactProfile, gb_to_bytes
from dots.serving.policy import (
    ServingWorkerChoice,
    ServingWorkerSnapshot,
    choose_load_worker,
    choose_serving_worker,
    replica_floor_for_resident_gb,
    replica_limit_for_resident_gb,
    should_start_pressure_replica,
)


def _artifact() -> BackendArtifactProfile:
    return BackendArtifactProfile(
        model_id="model",
        backend="mlx",
        source_bytes=gb_to_bytes(10.0),
        prepared_bytes=gb_to_bytes(10.0),
        scratch_bytes=0,
        resident_ram_bytes=gb_to_bytes(10.0),
        wire_dtype_bytes=2,
        metadata_source="test",
        source_is_prepared=True,
    )


def _snapshot(worker_id: str, **overrides) -> ServingWorkerSnapshot:
    values = {
        "worker_id": worker_id,
        "account_id": f"acc-{worker_id}",
        "ram_available_gb": 32.0,
        "ram_reserved_gb": 0.0,
        "disk_available_gb": 64.0,
        "evictable_cache_gb": 0.0,
        "compute_tflops": 4.0,
        "memory_bandwidth_gb_per_s": 200.0,
        "download_gb_per_s": 1.0,
        "disk_read_gb_per_s": 3.0,
        "hub_rtt_ms": 20.0,
        "supports_backend": True,
        "has_active_load": False,
        "has_loaded_model": False,
        "has_source_artifact": False,
        "has_prepared_artifact": False,
    }
    values.update(overrides)
    return ServingWorkerSnapshot(**values)


def test_choose_serving_worker_prefers_self_owned_when_close_enough():
    chosen = choose_serving_worker(
        (
            ServingWorkerChoice("remote", "acc-remote", active_turns=0, tps=100.0),
            ServingWorkerChoice("local", "acc-user", active_turns=0, tps=80.0),
        ),
        querier_account="acc-user",
    )

    assert chosen == "local"


def test_choose_serving_worker_returns_none_without_candidates():
    assert choose_serving_worker((), querier_account="acc-user") is None


def test_choose_serving_worker_prefers_remote_when_self_is_too_slow():
    chosen = choose_serving_worker(
        (
            ServingWorkerChoice("remote", "acc-remote", active_turns=0, tps=100.0),
            ServingWorkerChoice("local", "acc-user", active_turns=0, tps=20.0),
        ),
        querier_account="acc-user",
    )

    assert chosen == "remote"


def test_choose_load_worker_rejects_workers_that_do_not_fit_artifact():
    artifact = _artifact()

    chosen = choose_load_worker(
        (
            _snapshot("small", ram_available_gb=4.0),
            _snapshot("wrong-backend", supports_backend=False),
            _snapshot("fit", compute_tflops=8.0),
        ),
        artifact,
        user_account="acc-other",
    )

    assert chosen == "fit"


def test_choose_load_worker_can_use_evictable_cache_for_disk_headroom():
    artifact = _artifact()

    chosen = choose_load_worker(
        (
            _snapshot(
                "cache-room",
                disk_available_gb=2.0,
                evictable_cache_gb=9.0,
            ),
        ),
        artifact,
        user_account="acc-other",
    )

    assert chosen == "cache-room"


def test_choose_load_worker_rejects_excluded_busy_loaded_and_disk_constrained():
    artifact = _artifact()

    chosen = choose_load_worker(
        (
            _snapshot("excluded"),
            _snapshot("busy", has_active_load=True),
            _snapshot("loaded", has_loaded_model=True),
            _snapshot("disk-small", disk_available_gb=1.0),
        ),
        artifact,
        user_account="acc-other",
        exclude_worker_ids={"excluded"},
    )

    assert chosen is None


def test_pressure_replica_policy_has_floor_and_load_guards():
    assert not should_start_pressure_replica(
        active_load_exists=True,
        ready_replica_count=1,
        ready_active_turns=100,
        resident_ram_gb=8.0,
    )
    assert should_start_pressure_replica(
        active_load_exists=False,
        ready_replica_count=1,
        ready_active_turns=0,
        resident_ram_gb=8.0,
    )


def test_pressure_replica_policy_stops_at_limit_and_uses_pressure_threshold():
    assert not should_start_pressure_replica(
        active_load_exists=False,
        ready_replica_count=48,
        ready_active_turns=1_000,
        resident_ram_gb=8.0,
    )
    assert not should_start_pressure_replica(
        active_load_exists=False,
        ready_replica_count=24,
        ready_active_turns=10,
        resident_ram_gb=8.0,
    )
    assert should_start_pressure_replica(
        active_load_exists=False,
        ready_replica_count=24,
        ready_active_turns=60,
        resident_ram_gb=8.0,
    )


def test_replica_floor_buckets_are_explicit():
    assert replica_floor_for_resident_gb(0.5) == 48
    assert replica_floor_for_resident_gb(1.5) == 40
    assert replica_floor_for_resident_gb(3.0) == 32
    assert replica_floor_for_resident_gb(8.0) == 24
    assert replica_floor_for_resident_gb(12.0) == 18
    assert replica_floor_for_resident_gb(32.0) == 12
    assert replica_floor_for_resident_gb(658.0) == 3
    assert replica_limit_for_resident_gb(8.0) == 48

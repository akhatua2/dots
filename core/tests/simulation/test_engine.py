from __future__ import annotations

import pytest

from dots.backends.artifacts import BackendArtifactProfile, gb_to_bytes

from tests.simulation.framework import SimClock, SimConstraintError, SimPool
from tests.simulation.scenarios import mlx_artifact, model, user, worker


def _conversion_artifact(model_id: str) -> BackendArtifactProfile:
    return BackendArtifactProfile(
        model_id=model_id,
        backend="mlx",
        source_bytes=gb_to_bytes(8.0),
        prepared_bytes=gb_to_bytes(4.0),
        scratch_bytes=gb_to_bytes(2.0),
        resident_ram_bytes=gb_to_bytes(4.0),
        wire_dtype_bytes=2,
        metadata_source="engine conversion fixture",
    )


def _pool_with_worker_and_model():
    pool = SimPool(SimClock())
    sim_model = model()
    artifact = _conversion_artifact(sim_model.model_id)
    sim_worker = worker("alice")
    pool.register_model(sim_model)
    pool.register_artifact(artifact)
    pool.worker_join(sim_worker)
    return pool, sim_worker, sim_model


def test_virtual_clock_orders_same_timestamp_events_deterministically():
    clock = SimClock()
    seen: list[str] = []

    clock.schedule(1.0, lambda: seen.append("first"))
    clock.schedule(1.0, lambda: seen.append("second"))
    clock.schedule(0.5, lambda: seen.append("zero"))
    clock.run_until_idle()

    assert seen == ["zero", "first", "second"]
    assert clock.now_s == pytest.approx(1.0)


def test_cold_load_uses_exact_virtual_phase_durations():
    pool, sim_worker, sim_model = _pool_with_worker_and_model()

    load = pool.start_load(sim_worker.worker_id, sim_model.model_id)
    pool.clock.run_until_idle()

    assert [phase.name for phase in load.phases] == [
        "backend_setup",
        "source_virtual_download",
        "source_cache_write",
        "source_read_for_conversion",
        "artifact_conversion",
        "prepared_artifact_write",
        "weight_load_read",
    ]
    assert load.completed_at_s == pytest.approx(20.0)
    assert sim_worker.ram_reserved_gb == pytest.approx(4.0)
    assert sim_worker.disk_reserved_gb == pytest.approx(12.0)


def test_cached_prepared_artifact_skips_download_and_conversion():
    pool, sim_worker, sim_model = _pool_with_worker_and_model()
    pool.seed_installed_backend(sim_worker.worker_id, "mlx")
    pool.seed_source_artifact(sim_worker.worker_id, sim_model.model_id)
    pool.seed_prepared_artifact(sim_worker.worker_id, sim_model.model_id, "mlx")

    load = pool.start_load(sim_worker.worker_id, sim_model.model_id)
    pool.clock.run_until_idle()

    assert [phase.name for phase in load.phases] == ["weight_load_read"]
    assert load.completed_at_s == pytest.approx(1.0)


def test_turn_updates_metrics_satisfaction_and_zero_sum_ledger():
    pool, sim_worker, sim_model = _pool_with_worker_and_model()
    sim_user = user("bob")
    pool.users[sim_user.user_id] = sim_user

    pool.start_load(sim_worker.worker_id, sim_model.model_id)
    pool.clock.run_until_idle()
    turn = pool.start_turn(
        sim_user.user_id,
        sim_model.model_id,
        input_tokens=10,
        output_tokens=20,
    )
    pool.clock.run_until_idle()

    metrics = pool.metrics()
    assert turn.status == "completed"
    assert metrics.completed_turns == 1
    assert metrics.failed_turns == 0
    assert metrics.total_cu_spent == pytest.approx(0.06)
    assert metrics.ledger_total_balance_microcu == 0


def test_worker_leave_cancels_active_load_without_real_sleep():
    pool, sim_worker, sim_model = _pool_with_worker_and_model()

    load = pool.start_load(sim_worker.worker_id, sim_model.model_id)
    pool.clock.run_for(5.0)
    pool.worker_leave(sim_worker.worker_id)
    pool.clock.run_until_idle()

    assert load.canceled
    assert not pool.deployments[sim_model.model_id].ready_worker_ids
    assert pool.no_online_worker_exceeded_ram()
    assert pool.no_online_worker_exceeded_disk()


def test_cache_reaper_evicts_stale_artifact_before_new_load():
    pool = SimPool(SimClock())
    stale_model = model("stale-model")
    new_model = model("new-model")
    stale_artifact = mlx_artifact(
        stale_model.model_id,
        source_gb=10.0,
        size_provenance="stale cache fixture",
    )
    new_artifact = mlx_artifact(
        new_model.model_id,
        source_gb=8.0,
        size_provenance="new cache fixture",
    )
    sim_worker = worker("alice", disk_gb=12.0, ram_gb=32.0)
    for sim_model, artifact in (
        (stale_model, stale_artifact),
        (new_model, new_artifact),
    ):
        pool.register_model(sim_model)
        pool.register_artifact(artifact)
    pool.worker_join(sim_worker)
    pool.seed_source_artifact(sim_worker.worker_id, stale_model.model_id)

    load = pool.start_load(sim_worker.worker_id, new_model.model_id)

    assert load.reserved_disk_gb == 8.0
    assert (
        stale_model.model_id,
        stale_artifact.backend,
    ) not in sim_worker.source_artifacts
    assert sim_worker.disk_reserved_gb == pytest.approx(8.0)
    assert any(event.kind == "cache_evicted" for event in pool.event_log)
    assert pool.no_online_worker_exceeded_disk()


def test_hard_constraints_reject_insufficient_disk_and_backend_mismatch():
    pool = SimPool(SimClock())
    sim_model = model()
    pool.register_model(sim_model)
    pool.register_artifact(_conversion_artifact(sim_model.model_id))
    low_disk = worker("low-disk", disk_gb=8.0)
    cpu_only = worker("cpu-only", backends=frozenset({"cpu"}))
    pool.worker_join(low_disk)
    pool.worker_join(cpu_only)

    with pytest.raises(SimConstraintError, match="disk exceeded"):
        pool.start_load(low_disk.worker_id, sim_model.model_id)
    with pytest.raises(SimConstraintError, match="does not support backend"):
        pool.start_load(cpu_only.worker_id, sim_model.model_id)


def test_worker_drop_in_active_serving_chain_fails_turn_and_releases_workers():
    pool = SimPool(SimClock())
    sim_model = model()
    sim_user = user("bob")
    pool.register_model(sim_model)
    pool.register_artifact(_conversion_artifact(sim_model.model_id))
    pool.users[sim_user.user_id] = sim_user
    alice = worker("alice")
    dana = worker("dana")
    alice.peer_rtt_ms[dana.worker_id] = 1.0
    alice.peer_bandwidth_gb_per_s[dana.worker_id] = 10.0
    dana.peer_rtt_ms[alice.worker_id] = 1.0
    dana.peer_bandwidth_gb_per_s[alice.worker_id] = 10.0
    for sim_worker in (alice, dana):
        pool.worker_join(sim_worker)
        pool.seed_installed_backend(sim_worker.worker_id, "mlx")
        pool.seed_source_artifact(sim_worker.worker_id, sim_model.model_id)
        pool.seed_prepared_artifact(sim_worker.worker_id, sim_model.model_id, "mlx")
        pool.start_load(sim_worker.worker_id, sim_model.model_id)
    pool.clock.run_until_idle()

    turn = pool.start_sharded_turn(
        sim_user.user_id,
        sim_model.model_id,
        worker_ids=(alice.worker_id, dana.worker_id),
        input_tokens=5,
        output_tokens=100,
    )
    pool.clock.run_for((turn.completed_at_s - turn.started_at_s) / 2)
    pool.worker_leave(dana.worker_id)
    pool.clock.run_until_idle()

    assert turn.status == "failed"
    assert alice.active_turns == 0
    assert dana.active_turns == 0
    assert pool.metrics().total_cu_spent == 0.0

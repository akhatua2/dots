from __future__ import annotations

from collections import Counter, defaultdict

import pytest

from tests.simulation.bruteforce import brute_force_static_frontier
from tests.simulation.framework import SimClock, SimPool
from tests.simulation.reports import build_report
from tests.simulation.scenarios import (
    EXTREME_PROVIDER_ACCOUNT,
    FLASH_CROWD_MODEL_ID,
    OVERLOAD_DEMAND_MULTIPLIER,
    PEAK_TRAFFIC_REQUESTS,
    SIMULATION_HORIZON_S,
    UPGRADE_TARGET_MODEL_ID,
    compare_single_turn_choice,
    measured_model_catalog,
    model,
    mlx_artifact,
    run_large_dynamic_pool,
    user,
    worker,
)

# These are simulations / benchmarks, not unit tests — they model large pools
# over time and take minutes. Excluded from the PR gate via `simulation`; run
# them explicitly with `-m simulation` (or on a schedule).
pytestmark = pytest.mark.simulation


def test_single_turn_choice_report_measures_known_suboptimality_gap():
    comparison = compare_single_turn_choice()

    assert comparison.baseline.strategy == "slow_local"
    assert comparison.candidate.strategy == "fast_remote"
    assert (
        comparison.candidate.mean_satisfaction > comparison.baseline.mean_satisfaction
    )
    assert comparison.satisfaction_gap > 0.0
    assert comparison.failure_gap == 0.0


def test_real_world_large_pool_run_generates_metrics_report():
    # Heavy simulation: 200 workers × 100 users, ~3 min (module marked slow).
    pool = run_large_dynamic_pool(n_workers=200, n_users=100)
    report = build_report(
        pool,
        scenario="large_dynamic_pool",
        strategy="dynamic_on_demand_reuse",
    )
    event_counts = Counter(event.kind for event in pool.event_log)
    decisions = Counter(
        event.detail.get("decision")
        for event in pool.event_log
        if event.kind == "request_served"
    )

    assert report.sim_time_s == SIMULATION_HORIZON_S
    assert event_counts["request_received"] == PEAK_TRAFFIC_REQUESTS
    assert PEAK_TRAFFIC_REQUESTS * 0.9 < report.completed_turns < PEAK_TRAFFIC_REQUESTS
    assert event_counts["request_skipped_user_offline"] > 0
    assert event_counts["request_cold_load_started"] >= len(pool.models)
    assert event_counts["pressure_replica_started"] > 0
    assert event_counts["cache_reaper_ran"] > 0
    incident_labels = {
        str(event.detail["label"])
        for event in pool.event_log
        if event.kind == "incident_window_registered"
    }
    assert {
        "many_small_workers_huge_model",
        "network_partition",
        "slow_disk_death",
        "partial_cache_corruption",
        "hot_model_no_free_ram",
        "asymmetric_network",
        "competing_model_priorities",
        "model_upgrade_swap",
        "whale_worker_churn",
        "medium_model_flash_crowd",
        "provider_fairness_concentration",
    } <= incident_labels
    assert event_counts["incident_huge_model_small_workers_rejected"] == 1
    assert event_counts["incident_network_partition_started"] == 1
    assert event_counts["incident_network_partition_finished"] == 1
    assert event_counts["incident_slow_disk_started"] == 1
    assert event_counts["incident_slow_disk_finished"] == 1
    assert event_counts["incident_cache_corrupted"] == 1
    assert event_counts["incident_hot_model_no_free_ram_started"] == 1
    assert event_counts["incident_hot_model_no_free_ram_finished"] == 1
    assert event_counts["incident_asymmetric_network_started"] == 1
    assert event_counts["incident_asymmetric_network_finished"] == 1
    assert event_counts["incident_competing_model_priorities_started"] == 1
    assert event_counts["incident_competing_model_priorities_finished"] == 1
    assert event_counts["incident_model_upgrade_started"] == 1
    assert event_counts["incident_model_upgrade_finished"] == 1
    assert event_counts["incident_whale_worker_churn_started"] == 1
    assert event_counts["incident_whale_worker_churn_finished"] == 1
    assert event_counts["incident_medium_model_flash_crowd_started"] == 1
    assert event_counts["incident_medium_model_flash_crowd_finished"] == 1
    assert event_counts["incident_provider_fairness_reported"] == 1
    incident_windows = [
        (
            str(event.detail["label"]),
            float(event.detail["start_s"]),
            float(event.detail["end_s"]),
        )
        for event in pool.event_log
        if event.kind == "incident_window_registered"
    ]
    assert any(
        left_label != right_label and left_start < right_end and right_start < left_end
        for left_label, left_start, left_end in incident_windows
        for right_label, right_start, right_end in incident_windows
    )
    incident_detail = {
        event.kind: event.detail
        for event in pool.event_log
        if event.kind.startswith("incident_")
    }
    assert (
        int(
            incident_detail["incident_huge_model_small_workers_rejected"][
                "rejected_workers"
            ]
        )
        > 0
    )
    assert (
        int(incident_detail["incident_network_partition_started"]["affected_workers"])
        > 0
    )
    assert int(incident_detail["incident_slow_disk_started"]["affected_workers"]) > 0
    assert float(incident_detail["incident_cache_corrupted"]["released_gb"]) > 0.0
    assert (
        float(
            incident_detail["incident_hot_model_no_free_ram_started"]["reserved_ram_gb"]
        )
        > 0.0
    )
    assert (
        float(
            incident_detail["incident_provider_fairness_reported"]["top_provider_share"]
        )
        > 0.0
    )
    assert decisions["pipeline_reuse"] > 0
    assert event_counts["worker_leave"] > 0
    assert event_counts["user_leave"] > 0
    assert report.mean_satisfaction > 0.0
    assert report.total_cu_spent > 0.0
    assert report.ram_utilization > 0.0
    assert report.disk_utilization > 0.0
    assert report.peak_worker_busy_utilization >= 0.9
    assert pool.metrics().ledger_total_balance_microcu == 0
    high_pressure_samples = [
        sample
        for sample in pool.metric_samples
        if sample.metrics.active_turns >= len(pool.workers) * 0.75
    ]
    assert len(high_pressure_samples) >= 3
    assert report.peak_model_queue_pressure > 0.0
    assert report.peak_active_worker_utilization > 0.0
    assert report.unused_ready_replicas > 0
    assert report.peak_replica_imbalance > 0.0
    assert len(pool.metric_samples) >= 35
    assert len(pool.models) >= 5
    assert "mlx-community/Kimi-K2.5" in pool.models
    assert "mlx-community/Qwen3.6-35B-A3B-8bit" in pool.models
    assert len(pool.artifacts) == len(pool.models)
    assert all(artifact.metadata_source for artifact in pool.artifacts.values())
    assert {
        "source_virtual_download",
        "source_cache_write",
        "weight_load_read",
    } <= {phase.name for load in pool.loads.values() for phase in load.phases}
    completed_model_ids = {
        turn.model_id for turn in pool.turns.values() if turn.status == "completed"
    }
    assert completed_model_ids == set(pool.models)
    assert "mlx-community/Kimi-K2.5" in completed_model_ids
    assert "mlx-community/Qwen3.6-35B-A3B-8bit" in completed_model_ids
    assert UPGRADE_TARGET_MODEL_ID in completed_model_ids
    flash_loads = [
        load for load in pool.loads.values() if load.model_id == FLASH_CROWD_MODEL_ID
    ]
    flash_ready_worker_ids = {
        load.worker_id for load in flash_loads if load.completed_at_s is not None
    }
    flash_turns = [
        turn
        for turn in pool.turns.values()
        if turn.model_id == FLASH_CROWD_MODEL_ID and turn.status == "completed"
    ]
    assert len(flash_ready_worker_ids) >= 20
    assert len(flash_turns) > PEAK_TRAFFIC_REQUESTS * 0.2
    assert (
        sum(
            1
            for event in pool.event_log
            if event.kind == "pressure_replica_started"
            and event.detail["model_id"] == FLASH_CROWD_MODEL_ID
        )
        >= 20
    )
    assert len({turn.worker_id for turn in flash_turns}) >= 10
    kimi_worker_ids = {
        load.worker_id
        for load in pool.loads.values()
        if load.model_id == "mlx-community/Kimi-K2.5"
    }
    assert 0 < len(kimi_worker_ids) < len(pool.workers) * 0.05
    for worker_id in kimi_worker_ids:
        sim_worker = pool.workers[worker_id]
        assert sim_worker.ram_gb >= 658.0
        assert sim_worker.disk_gb >= 658.0
    whale_workers = [
        worker
        for worker in pool.workers.values()
        if worker.account_id == EXTREME_PROVIDER_ACCOUNT
    ]
    assert len(whale_workers) == 1
    whale = whale_workers[0]
    whale_join_times = [
        event.time_s
        for event in pool.event_log
        if event.kind == "worker_join" and event.detail["worker_id"] == whale.worker_id
    ]
    whale_leave_times = [
        event.time_s
        for event in pool.event_log
        if event.kind == "worker_leave" and event.detail["worker_id"] == whale.worker_id
    ]
    assert whale_join_times and whale_leave_times
    assert whale_join_times[0] < whale_leave_times[0]
    whale_loads = [
        load for load in pool.loads.values() if load.worker_id == whale.worker_id
    ]
    assert {load.model_id for load in whale_loads} == set(pool.models)
    assert all(load.completed_at_s is not None for load in whale_loads)
    whale_turns = [
        turn for turn in pool.turns.values() if whale.worker_id in turn.worker_ids
    ]
    assert len(whale_turns) > 10_000
    assert sum(
        1
        for event in pool.event_log
        if event.kind == "model_unloaded"
        and event.detail["worker_id"] == whale.worker_id
    ) == len(pool.models)
    post_whale_leave_turns = [
        turn
        for turn in pool.turns.values()
        if turn.status == "completed" and turn.started_at_s > whale_leave_times[0]
    ]
    assert post_whale_leave_turns
    assert all(
        whale.worker_id not in turn.worker_ids for turn in post_whale_leave_turns
    )
    assert whale.ram_reserved_gb == pytest.approx(0.0, abs=1e-6)
    assert whale.disk_reserved_gb >= 658.0
    assert pool.metrics().ledger_balances[EXTREME_PROVIDER_ACCOUNT] > 0.0
    worker_join_times = {
        event.time_s for event in pool.event_log if event.kind == "worker_join"
    }
    user_join_times = {
        event.time_s for event in pool.event_log if event.kind == "user_join"
    }
    assert len(worker_join_times) > 20
    assert len(user_join_times) > 20
    users_with_repeated_sessions = Counter(
        event.detail["user_id"] for event in pool.event_log if event.kind == "user_join"
    )
    workers_with_repeated_sessions = Counter(
        event.detail["worker_id"]
        for event in pool.event_log
        if event.kind == "worker_join"
    )
    assert max(users_with_repeated_sessions.values()) > 1
    assert max(workers_with_repeated_sessions.values()) > 1
    worker_locations = {
        event.detail["location"]
        for event in pool.event_log
        if event.kind == "worker_join"
    }
    assert len(worker_locations) >= 10
    chat_user_ids = {
        event.detail["user_id"] for event in pool.event_log if event.kind == "user_join"
    }
    owner_user_ids = {
        event.detail["owner_user_id"]
        for event in pool.event_log
        if event.kind == "worker_join" and event.detail["owner_user_id"] is not None
    }
    assert chat_user_ids - owner_user_ids
    assert any(
        event.detail["owner_user_id"] is None
        for event in pool.event_log
        if event.kind == "worker_join"
    )
    workers_by_account: dict[str, int] = defaultdict(int)
    for event in pool.event_log:
        if event.kind == "worker_join":
            workers_by_account[str(event.detail["account_id"])] += 1
    assert max(workers_by_account.values()) > 1
    peer_rtts = [
        rtt
        for sim_worker in pool.workers.values()
        for rtt in sim_worker.peer_rtt_ms.values()
    ]
    assert min(peer_rtts) < 2.0
    assert max(peer_rtts) > 100.0
    traffic_multipliers = {
        float(event.detail["demand_multiplier"])
        for event in pool.event_log
        if event.kind == "traffic_window_registered"
    }
    assert 0.0 in traffic_multipliers
    assert OVERLOAD_DEMAND_MULTIPLIER in traffic_multipliers
    assert max(sample.metrics.active_turns for sample in pool.metric_samples) > 0


def test_real_world_large_pool_is_deterministic_for_same_inputs():
    first = run_large_dynamic_pool(n_workers=60, n_users=30, n_turns=200, n_models=6)
    second = run_large_dynamic_pool(n_workers=60, n_users=30, n_turns=200, n_models=6)

    def summary(pool):
        report = build_report(
            pool,
            scenario="large_dynamic_pool",
            strategy="dynamic_on_demand_reuse",
        )
        return (
            report.sim_time_s,
            report.completed_turns,
            report.failed_turns,
            report.mean_satisfaction,
            report.total_cu_spent,
            Counter(event.kind for event in pool.event_log),
        )

    assert summary(first) == summary(second)


def test_small_static_simulation_can_expose_scheduler_frontier_for_gap_analysis():
    pool = SimPool(SimClock())
    sim_model = model()
    sim_artifact = mlx_artifact(
        sim_model.model_id,
        source_gb=12.0,
        size_provenance="static frontier fixture artifact profile",
    )
    sim_user = user("bob")
    pool.register_model(sim_model)
    pool.register_artifact(sim_artifact)
    pool.users[sim_user.user_id] = sim_user
    workers = (
        worker("alice", ram_gb=32.0, disk_gb=64.0, compute_tflops=1.0),
        worker("bob", ram_gb=32.0, disk_gb=64.0, compute_tflops=2.0),
        worker("low-disk", ram_gb=32.0, disk_gb=8.0, compute_tflops=10.0),
    )
    for left in workers:
        for right in workers:
            if left.worker_id == right.worker_id:
                continue
            left.peer_rtt_ms[right.worker_id] = 1.0
            left.peer_bandwidth_gb_per_s[right.worker_id] = 10.0
    for sim_worker in workers:
        pool.worker_join(sim_worker)

    frontier, chosen = brute_force_static_frontier(
        pool,
        sim_model.model_id,
        user_id=sim_user.user_id,
        unit_count=4,
        max_segments=3,
    )

    assert frontier
    assert chosen in frontier
    assert "low-disk" not in {worker_id for p in frontier for worker_id in p.workers}
    assert max(p.tps for p in frontier) + 1e-9 >= chosen.tps


def test_measured_catalog_is_offline_and_contains_large_moe_models():
    catalog = measured_model_catalog()
    by_model = {sim_model.model_id: artifact for sim_model, artifact in catalog}

    assert len(catalog) >= 5
    assert by_model["mlx-community/Qwen3.6-35B-A3B-8bit"].source_bytes == 37_700_000_000
    assert by_model["mlx-community/Kimi-K2.5"].source_bytes == 658_000_000_000
    assert all(artifact.source_is_prepared for artifact in by_model.values())

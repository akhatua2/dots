"""Scenario reporting helpers for comparing planner behavior over time."""

from __future__ import annotations

from dataclasses import dataclass

from .framework import SimPool


@dataclass(frozen=True)
class SimRunReport:
    scenario: str
    strategy: str
    sim_time_s: float
    event_count: int
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
    peak_active_worker_utilization: float
    peak_model_queue_pressure: float
    unused_ready_replicas: int
    peak_replica_imbalance: float


def build_report(
    pool: SimPool,
    *,
    scenario: str,
    strategy: str,
) -> SimRunReport:
    metrics = pool.metrics()
    sampled_metrics = [sample.metrics for sample in pool.metric_samples] + [metrics]
    return SimRunReport(
        scenario=scenario,
        strategy=strategy,
        sim_time_s=metrics.sim_time_s,
        event_count=len(pool.event_log),
        ram_utilization=metrics.ram_utilization,
        disk_utilization=metrics.disk_utilization,
        active_turns=metrics.active_turns,
        active_worker_utilization=metrics.active_worker_utilization,
        completed_turns=metrics.completed_turns,
        failed_turns=metrics.failed_turns,
        mean_satisfaction=metrics.mean_satisfaction,
        total_cu_spent=metrics.total_cu_spent,
        mean_worker_busy_utilization=metrics.mean_worker_busy_utilization,
        peak_worker_busy_utilization=metrics.peak_worker_busy_utilization,
        peak_active_worker_utilization=max(
            (sample.active_worker_utilization for sample in sampled_metrics),
            default=0.0,
        ),
        peak_model_queue_pressure=max(
            (
                max(sample.model_queue_pressure.values(), default=0.0)
                for sample in sampled_metrics
            ),
            default=0.0,
        ),
        unused_ready_replicas=sum(metrics.unused_ready_replicas.values()),
        peak_replica_imbalance=max(
            metrics.replica_imbalance.values(),
            default=0.0,
        ),
    )


def report_delta(
    baseline: SimRunReport,
    candidate: SimRunReport,
) -> dict[str, float]:
    if baseline.scenario != candidate.scenario:
        raise ValueError("reports must come from the same scenario")
    return {
        "sim_time_s": candidate.sim_time_s - baseline.sim_time_s,
        "event_count": float(candidate.event_count - baseline.event_count),
        "ram_utilization": candidate.ram_utilization - baseline.ram_utilization,
        "disk_utilization": candidate.disk_utilization - baseline.disk_utilization,
        "active_turns": float(candidate.active_turns - baseline.active_turns),
        "active_worker_utilization": (
            candidate.active_worker_utilization - baseline.active_worker_utilization
        ),
        "completed_turns": float(candidate.completed_turns - baseline.completed_turns),
        "failed_turns": float(candidate.failed_turns - baseline.failed_turns),
        "mean_satisfaction": (candidate.mean_satisfaction - baseline.mean_satisfaction),
        "total_cu_spent": candidate.total_cu_spent - baseline.total_cu_spent,
        "mean_worker_busy_utilization": (
            candidate.mean_worker_busy_utilization
            - baseline.mean_worker_busy_utilization
        ),
        "peak_worker_busy_utilization": (
            candidate.peak_worker_busy_utilization
            - baseline.peak_worker_busy_utilization
        ),
        "peak_active_worker_utilization": (
            candidate.peak_active_worker_utilization
            - baseline.peak_active_worker_utilization
        ),
        "peak_model_queue_pressure": (
            candidate.peak_model_queue_pressure - baseline.peak_model_queue_pressure
        ),
        "unused_ready_replicas": float(
            candidate.unused_ready_replicas - baseline.unused_ready_replicas
        ),
        "peak_replica_imbalance": (
            candidate.peak_replica_imbalance - baseline.peak_replica_imbalance
        ),
    }

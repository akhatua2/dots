"""Scheduler data classes.

Every type here is frozen / hashable / safely shareable. The scheduler
treats placements as values, not state — pure computation makes the
correctness invariants in tests/test_scheduler.py much easier to assert.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ResourceDecl:
    """What a worker offers the pool.

    `compute_tflops_per_sec` is *effective* throughput for the worker's
    backend on this kind of workload, not a vendor spec. For memory-bound
    4-bit MLX inference on Apple Silicon, this depends on measured chip
    behavior rather than vendor peak FLOPS.
    """

    worker_id: str
    account_id: str
    ram_gb_free: float
    compute_tflops_per_sec: float
    backends: frozenset[str]
    cluster_id: str = ""
    disk_gb_free: float = 0.0
    cpu_utilization: float = 0.0
    io_read_gbps: float = 0.0
    io_write_gbps: float = 0.0


@dataclass(frozen=True)
class WorkUnit:
    """One atom of the per-token compute DAG.

    v1 assumes a linear chain: embed → L0 → ... → LN → norm → lm_head → sample.
    Each unit has a fixed RAM footprint (weights it holds) and a per-token
    FLOP cost (the matmul / lookup it does each step).
    """

    unit_id: str
    ram_required_gb: float
    flops_per_token: float
    backend_required: frozenset[str] | None = None  # None = any backend


@dataclass(frozen=True)
class LatencyMatrix:
    """Pairwise RTT between workers, in milliseconds.

    Symmetric; same-worker is 0; disconnected pairs return math.inf.
    """

    rtt_ms: dict[tuple[str, str], float] = field(default_factory=dict)

    def get(self, w1: str, w2: str) -> float:
        if w1 == w2:
            return 0.0
        if (w1, w2) in self.rtt_ms:
            return self.rtt_ms[(w1, w2)]
        if (w2, w1) in self.rtt_ms:
            return self.rtt_ms[(w2, w1)]
        return math.inf

    def reachable(self, w1: str, w2: str) -> bool:
        return self.get(w1, w2) != math.inf


@dataclass(frozen=True)
class Placement:
    """An assignment of units to workers, with computed metrics."""

    # segments[i] = (worker_id, ordered tuple of unit_ids assigned).
    # Each segment is contiguous in the unit chain.
    segments: tuple[tuple[str, tuple[str, ...]], ...]

    cu_paid: float  # FLOPs paid to non-querier workers, per token
    tps: float  # estimated tokens-per-second
    compute_time_ms: float  # diagnostic
    network_time_ms: float  # diagnostic

    @property
    def k(self) -> int:
        return len(self.segments)

    @property
    def workers(self) -> tuple[str, ...]:
        return tuple(s[0] for s in self.segments)

    @property
    def all_local(self) -> bool:
        return self.k == 1

    def __repr__(self) -> str:
        parts = [f"{w}[{','.join(u)}]" for w, u in self.segments]
        return f"Placement({' → '.join(parts)} | CU={self.cu_paid:.3g}, TPS={self.tps:.1f})"

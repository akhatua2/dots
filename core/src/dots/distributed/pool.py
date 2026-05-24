"""Hub-side view of the worker pool: resources + latency matrix.

The `Pool` collects:
  * `ResourceDecl`s reported by each worker in HELLO
  * Pairwise RTT measurements arriving as probe results
  * Live worker membership (joins/leaves)

It is the input to the `Scheduler` (dots.scheduler) when planning a query.

Pool state is owned by the hub and mutated in async handlers, so callers
should treat it as not-thread-safe — the hub holds a lock when reading
during planning to avoid mid-update reads.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

from ..placement.scheduler import LatencyMatrix, ResourceDecl

log = logging.getLogger("dots.pool")
DEFAULT_UNMEASURED_RTT_MS = 5.0
_UNMEASURED_RTT_ENV = "DOTS_UNMEASURED_RTT_MS"


def _unmeasured_rtt_ms() -> float:
    raw = os.environ.get(_UNMEASURED_RTT_ENV)
    return DEFAULT_UNMEASURED_RTT_MS if raw is None else float(raw)


@dataclass
class LatencyCell:
    """One pairwise (W_a, W_b) latency measurement window."""

    rtt_p50_ms: float
    rtt_p99_ms: float = 0.0
    last_measured_unix: float = 0.0
    sample_count: int = 0


@dataclass
class Pool:
    """Hub-side state about every worker in the pool.

    Resources are keyed by worker_id (stable across reconnects).
    Latency is keyed by sorted (w_a, w_b) tuple for symmetry.
    """

    resources: dict[str, ResourceDecl] = field(default_factory=dict)
    latency: dict[tuple[str, str], LatencyCell] = field(default_factory=dict)

    # ────────────── membership ──────────────

    def add_or_update(self, worker_id: str, decl: ResourceDecl) -> None:
        self.resources[worker_id] = decl

    def remove(self, worker_id: str) -> None:
        self.resources.pop(worker_id, None)
        # Clear all latency cells touching this worker.
        stale = [k for k in self.latency if worker_id in k]
        for k in stale:
            self.latency.pop(k, None)

    def known_workers(self) -> list[str]:
        return list(self.resources.keys())

    # ────────────── latency ──────────────

    @staticmethod
    def _key(w_a: str, w_b: str) -> tuple[str, str]:
        return (w_a, w_b) if w_a <= w_b else (w_b, w_a)

    def record_probe(
        self,
        w_a: str,
        w_b: str,
        rtt_p50_ms: float,
        rtt_p99_ms: float = 0.0,
        sample_count: int = 1,
        now_unix: float | None = None,
    ) -> None:
        """Update the latency cell for (w_a, w_b)."""
        if w_a == w_b:
            return  # self-RTT is 0 by definition
        key = self._key(w_a, w_b)
        self.latency[key] = LatencyCell(
            rtt_p50_ms=float(rtt_p50_ms),
            rtt_p99_ms=float(rtt_p99_ms),
            last_measured_unix=float(now_unix) if now_unix is not None else time.time(),
            sample_count=int(sample_count),
        )

    def get_rtt(self, w_a: str, w_b: str) -> float | None:
        """Return p50 RTT or None if never measured."""
        if w_a == w_b:
            return 0.0
        cell = self.latency.get(self._key(w_a, w_b))
        return cell.rtt_p50_ms if cell is not None else None

    def stale_pairs(
        self, max_age_seconds: float, now_unix: float | None = None
    ) -> list[tuple[str, str]]:
        """Return (w_a, w_b) pairs whose latency cell is older than the threshold.

        Used by the hub to decide which pairs need a refresh probe.
        """
        if now_unix is None:
            now_unix = time.time()
        return [
            (a, b)
            for (a, b), cell in self.latency.items()
            if now_unix - cell.last_measured_unix > max_age_seconds
        ]

    def missing_pairs(self) -> list[tuple[str, str]]:
        """Return all (W_a, W_b) pairs that have no measurement yet."""
        ids = sorted(self.resources.keys())
        result: list[tuple[str, str]] = []
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                key = (ids[i], ids[j])
                if key not in self.latency:
                    result.append(key)
        return result

    # ────────────── scheduler hand-off ──────────────

    def snapshot(self) -> tuple[list[ResourceDecl], LatencyMatrix]:
        """Build an immutable snapshot suitable for scheduler input.

        Latency cells that have never been measured default to a configurable
        intra-pool RTT so the scheduler can still plan something instead of
        getting `inf`. Probes refine this quickly.
        """
        workers = list(self.resources.values())
        rtt: dict[tuple[str, str], float] = {}
        for (a, b), cell in self.latency.items():
            rtt[(a, b)] = cell.rtt_p50_ms
        # Fill missing pairs with an optimistic default so disconnected
        # cells aren't treated as infeasible.
        ids = [w.worker_id for w in workers]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                key = self._key(ids[i], ids[j])
                if key not in rtt:
                    rtt[key] = _unmeasured_rtt_ms()
        return workers, LatencyMatrix(rtt_ms=rtt)


# ─────────────────────── ResourceDecl from HELLO ────────────────────


def resource_decl_from_hello(
    hello: dict,
    worker_id: str,
) -> ResourceDecl | None:
    """Build a ResourceDecl from a HELLO message's `resources` field.

    Returns None if the HELLO didn't include a resources block.
    """
    res = hello.get("resources")
    if not isinstance(res, dict):
        return None
    return ResourceDecl(
        worker_id=worker_id,
        account_id=hello.get("account_id") or "",
        ram_gb_free=float(res.get("ram_gb_free", 0.0)),
        compute_tflops_per_sec=float(res.get("compute_tflops_per_sec", 0.0)),
        backends=frozenset(res.get("backends", [])),
        cluster_id=str(res.get("cluster_id", "")),
        disk_gb_free=float(res.get("disk_gb_free", 0.0)),
        cpu_utilization=float(res.get("cpu_utilization", 0.0)),
        io_read_gbps=float(res.get("io_read_gbps", 0.0)),
        io_write_gbps=float(res.get("io_write_gbps", 0.0)),
    )


def resource_decl_to_hello_field(decl: ResourceDecl) -> dict:
    """Inverse of `resource_decl_from_hello` — produces the HELLO `resources` dict."""
    return {
        "ram_gb_free": decl.ram_gb_free,
        "compute_tflops_per_sec": decl.compute_tflops_per_sec,
        "backends": sorted(decl.backends),
        "cluster_id": decl.cluster_id,
        "disk_gb_free": decl.disk_gb_free,
        "cpu_utilization": decl.cpu_utilization,
        "io_read_gbps": decl.io_read_gbps,
        "io_write_gbps": decl.io_write_gbps,
    }

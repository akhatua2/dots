"""Synthetic pool builders: Mac class profiles, latency tiers, factories."""
from __future__ import annotations

import random
from dataclasses import dataclass

from ..scheduler import LatencyMatrix, ResourceDecl


@dataclass(frozen=True)
class MacClass:
    """Effective throughput numbers for 4-bit MLX decode.

    Memory-bandwidth limited, not peak vendor TFLOPS. Calibrated against
    public mlx-lm benchmarks; ±20% accurate.
    """
    name: str
    ram_gb: float
    effective_tflops: float


MAC_CLASSES: dict[str, MacClass] = {
    "m1-air-8":     MacClass("M1 Air 8GB",        8,  0.4),
    "m1-pro-16":    MacClass("M1 Pro 16GB",      16,  0.8),
    "m1-max-32":    MacClass("M1 Max 32GB",      32,  1.5),
    "m2-air-8":     MacClass("M2 Air 8GB",        8,  0.5),
    "m2-air-16":    MacClass("M2 Air 16GB",      16,  0.5),
    "m2-pro-16":    MacClass("M2 Pro 16GB",      16,  1.0),
    "m2-pro-32":    MacClass("M2 Pro 32GB",      32,  1.2),
    "m3-max-48":    MacClass("M3 Max 48GB",      48,  2.5),
    "m3-max-96":    MacClass("M3 Max 96GB",      96,  2.8),
    "m2-ultra-128": MacClass("M2 Ultra 128GB", 128,  3.5),
}


# Realistic latency tiers (ms).
LATENCY_LAN = 1.0
LATENCY_OFFICE = 1.0
LATENCY_TAILNET = 80.0
LATENCY_OFFICE_TO_HOME = 30.0
LATENCY_WAN = 200.0


def make_worker(
    worker_id: str,
    account_id: str,
    klass: str,
    cluster_id: str = "",
    ram_used_gb: float = 3.0,
    backends: frozenset[str] = frozenset({"mlx"}),
) -> ResourceDecl:
    """One Mac of the given class with `ram_used_gb` reserved for OS+apps."""
    mc = MAC_CLASSES[klass]
    free = max(0.0, mc.ram_gb - ram_used_gb)
    return ResourceDecl(
        worker_id=worker_id,
        account_id=account_id,
        ram_gb_free=free,
        compute_tflops_per_sec=mc.effective_tflops,
        backends=backends,
        cluster_id=cluster_id,
    )


def make_simple_pool() -> tuple[list[ResourceDecl], LatencyMatrix]:
    """3-machine LAN pool: alice (strong) / bob (medium) / carol (weak)."""
    workers = [
        make_worker("alice", "acc-alice", "m3-max-48", cluster_id="A"),
        make_worker("bob",   "acc-bob",   "m2-pro-16", cluster_id="A"),
        make_worker("carol", "acc-carol", "m1-air-8",  cluster_id="A"),
    ]
    rtt = {
        ("alice", "bob"):   LATENCY_LAN,
        ("alice", "carol"): LATENCY_LAN,
        ("bob",   "carol"): LATENCY_LAN,
    }
    return workers, LatencyMatrix(rtt_ms=rtt)


def make_multi_cluster_pool() -> tuple[list[ResourceDecl], LatencyMatrix]:
    """10-machine pool across 3 latency tiers."""
    workers = [
        # Cluster A — home LAN
        make_worker("arpan-laptop",   "acc-arpan",     "m2-air-8",   "A"),
        make_worker("arpan-mac-mini", "acc-arpan",     "m2-pro-16",  "A"),
        make_worker("gizmo",          "acc-friend1",   "m3-max-48",  "A"),
        # Cluster B — remote friend, 80ms tailnet
        make_worker("friend-mbp",     "acc-friend2",   "m2-pro-32",  "B"),
        make_worker("friend-mini-1",  "acc-friend2",   "m1-pro-16",  "B"),
        make_worker("friend-mini-2",  "acc-friend2",   "m1-air-8",   "B"),
        # Cluster C — office, 30ms WAN
        make_worker("office-mac-1",   "acc-colleague", "m3-max-48",  "C"),
        make_worker("office-mac-2",   "acc-colleague", "m2-pro-16",  "C"),
    ]
    rtt: dict[tuple[str, str], float] = {}

    def ids_in(cluster: str) -> list[str]:
        return [w.worker_id for w in workers if w.cluster_id == cluster]

    for cluster in ("A", "B", "C"):
        ids = ids_in(cluster)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                rtt[(ids[i], ids[j])] = LATENCY_LAN

    for a in ids_in("A"):
        for b in ids_in("B"):
            rtt[(a, b)] = LATENCY_TAILNET
        for c in ids_in("C"):
            rtt[(a, c)] = LATENCY_OFFICE_TO_HOME
    for b in ids_in("B"):
        for c in ids_in("C"):
            rtt[(b, c)] = LATENCY_TAILNET + LATENCY_OFFICE_TO_HOME

    return workers, LatencyMatrix(rtt_ms=rtt)


def make_random_pool(
    n_workers: int,
    n_clusters: int = 3,
    seed: int = 0,
    klass_choices: list[str] | None = None,
) -> tuple[list[ResourceDecl], LatencyMatrix]:
    rng = random.Random(seed)
    klasses = klass_choices or list(MAC_CLASSES.keys())
    workers: list[ResourceDecl] = []
    for i in range(n_workers):
        cluster = f"C{i % n_clusters}"
        workers.append(make_worker(
            f"w{i}", f"acc-{i}",
            rng.choice(klasses), cluster_id=cluster,
        ))
    rtt: dict[tuple[str, str], float] = {}
    for i in range(n_workers):
        for j in range(i + 1, n_workers):
            same = workers[i].cluster_id == workers[j].cluster_id
            rtt[(workers[i].worker_id, workers[j].worker_id)] = (
                LATENCY_LAN if same else rng.uniform(30, 200)
            )
    return workers, LatencyMatrix(rtt_ms=rtt)

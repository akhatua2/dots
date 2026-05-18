"""Tensor-parallel sharding (TP) — scaffold only.

Per-unit matmul split across N workers; results all-reduced. Requires
fast interconnect (NVLink-class). Not implemented in v1 because:

  • The worker-side all-reduce primitive doesn't exist yet (post
    hybrid-pipeline plan).
  • The scheduler's enumeration currently maps unit → one worker; TP
    needs K-way assignment of a single unit, which is a different
    Placement shape.
  • Consumer LAN cannot move the per-layer activation traffic TP needs.

The class is here so the strategy registry can return it and tests can
exercise the profile machinery without TP code paths actually running.
"""
from __future__ import annotations

from .base import ShardingStrategy, StrategyProfile


class TensorParallel(ShardingStrategy):
    profile = StrategyProfile(
        name="tp",
        needs_high_bandwidth=True,
        needs_moe_model=False,
        works_across_clusters=False,
    )

    def build_unit_graph(self, geom, *, layers_per_block=1):
        raise NotImplementedError(
            "TensorParallel is scaffolded; v1 has no TP-aware unit builder."
        )

    def plan(self, units, workers, latency,
             querier_account=None, mode="cheap", max_segments=None):
        raise NotImplementedError(
            "TensorParallel is scaffolded; needs an all-reduce-aware scheduler."
        )

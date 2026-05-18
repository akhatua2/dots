"""Sharding strategies for distributing a model across a pool.

A strategy describes *how* a model's per-token work is split across
workers. The compute plane picks one based on model architecture and
pool topology; the scheduler enumerates placements within the chosen
strategy.

Strategies:
  pipeline (PP)  — each unit on one worker; hidden states flow through.
                   Fully implemented; default for v1.
  tensor   (TP)  — each unit's matmul split across workers, all-reduced.
                   Scaffold; needs an all-reduce primitive (post hybrid-pipeline).
  expert   (EP)  — MoE only; experts distributed, router dispatches per token.
                   Scaffold; needs MoE-aware geometry and per-expert units.
"""
from __future__ import annotations

import logging

from .base import ShardingStrategy, StrategyProfile
from .expert import ExpertParallel
from .pipeline import PipelineParallel
from .tensor import TensorParallel
from ..work_units import ModelGeometry

log = logging.getLogger("dots.sharding")


# Order matters: registry iteration is the planner's preference when
# multiple strategies are feasible. PP is the default; TP/EP are only
# chosen if explicitly requested or if they win the cost race (which
# they cannot today since they raise NotImplementedError).
_REGISTRY: tuple[ShardingStrategy, ...] = (
    PipelineParallel(),
    ExpertParallel(),
    TensorParallel(),
)


def available_strategies() -> tuple[ShardingStrategy, ...]:
    """Snapshot of the registry."""
    return _REGISTRY


def strategy_by_name(name: str) -> ShardingStrategy:
    """Look up a strategy by its profile name (`pp` / `tp` / `ep`)."""
    for s in _REGISTRY:
        if s.profile.name == name:
            return s
    raise KeyError(f"unknown sharding strategy: {name}")


def pick_strategy(
    *,
    is_moe: bool,
    has_high_bandwidth_tier: bool,
    explicit: str | None = None,
) -> ShardingStrategy:
    """Choose the strategy for a workload based on its profile.

    `explicit` (e.g. user-pinned via config) wins if set.
    Otherwise:
      • MoE model → ExpertParallel if implemented, else PP.
      • High-bandwidth tier present → TensorParallel if implemented, else PP.
      • Default → PipelineParallel.
    """
    if explicit is not None:
        return strategy_by_name(explicit)
    if is_moe:
        try:
            ep = strategy_by_name("ep")
            ep.build_unit_graph(ModelGeometry(1, 1, 1))
            return ep
        except NotImplementedError:
            log.debug("MoE detected but ExpertParallel not implemented; PP")
    if has_high_bandwidth_tier:
        try:
            tp = strategy_by_name("tp")
            tp.build_unit_graph(ModelGeometry(1, 1, 1))
            return tp
        except NotImplementedError:
            log.debug("HB tier present but TensorParallel not implemented; PP")
    return strategy_by_name("pp")


__all__ = [
    "ShardingStrategy",
    "StrategyProfile",
    "PipelineParallel",
    "TensorParallel",
    "ExpertParallel",
    "available_strategies",
    "strategy_by_name",
    "pick_strategy",
]

"""Base interface for sharding strategies.

A strategy is stateless: it produces an immutable unit graph for a
geometry, and given a pool snapshot it produces an immutable placement.
The compute plane composes strategy + scheduler + workers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Sequence

from ..scheduler import LatencyMatrix, Placement, ResourceDecl, WorkUnit
from ..work_units import ModelGeometry


@dataclass(frozen=True)
class StrategyProfile:
    """How a strategy describes itself to the planner.

    The planner uses these flags to filter strategies the pool can't
    actually run (e.g. no high-bandwidth tier → reject TP).
    """

    name: str
    needs_high_bandwidth: bool = False  # TP-class: NVLink / >100 Gbps
    needs_moe_model: bool = False  # EP only meaningful for MoE
    works_across_clusters: bool = True  # cross-LAN feasibility


class ShardingStrategy(ABC):
    """Strategies build unit graphs and ask the scheduler for placements."""

    profile: StrategyProfile

    @abstractmethod
    def build_unit_graph(
        self,
        geom: ModelGeometry,
        *,
        layers_per_block: int = 1,
    ) -> list[WorkUnit]: ...

    @abstractmethod
    def plan(
        self,
        units: Sequence[WorkUnit],
        workers: Sequence[ResourceDecl],
        latency: LatencyMatrix,
        querier_account: str | None = None,
        mode: str = "cheap",
        max_segments: int | None = None,
    ) -> Placement | None: ...

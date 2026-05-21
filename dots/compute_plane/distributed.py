"""Multi-worker compute plane.

Construction picks a sharding strategy and a placement based on the
pool's resource + latency graph; runtime per-unit dispatch follows the
placement table.

Status: placement planning is functional. Per-unit remote dispatch
(`generate`) is scaffolded — the worker-side `compute_unit` handler
ships in the hybrid-pipeline PR.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

from .base import ComputePlane, GenerationStats, VirtualResources
from ..placement.scheduler import LatencyMatrix, Placement, ResourceDecl
from ..placement.sharding import pick_strategy
from ..placement.work_units import ModelGeometry

log = logging.getLogger("dots.compute_plane.distributed")
HIGH_BANDWIDTH_RTT_MS = 1.0


@dataclass
class _RoutingTable:
    """unit_id → worker_id for the lifetime of one placement."""

    unit_to_worker: dict[str, str] = field(default_factory=dict)
    workers_by_id: dict[str, ResourceDecl] = field(default_factory=dict)

    @classmethod
    def from_placement(
        cls,
        placement: Placement,
        workers: Sequence[ResourceDecl],
    ) -> "_RoutingTable":
        wbid = {w.worker_id: w for w in workers}
        u2w: dict[str, str] = {}
        for worker_id, unit_ids in placement.segments:
            for uid in unit_ids:
                u2w[uid] = worker_id
        return cls(unit_to_worker=u2w, workers_by_id=wbid)

    def owner_of(self, unit_id: str) -> ResourceDecl | None:
        wid = self.unit_to_worker.get(unit_id)
        if wid is None:
            return None
        return self.workers_by_id.get(wid)


class DistributedComputePlane(ComputePlane):
    """Multi-worker plane.

    Construction picks a sharding strategy via `pick_strategy` and asks
    the scheduler for a placement. Runtime dispatch follows the routing
    table.
    """

    def __init__(
        self,
        geometry: ModelGeometry,
        workers: Sequence[ResourceDecl],
        latency: LatencyMatrix,
        *,
        is_moe: bool = False,
        explicit_strategy: str | None = None,
        querier_account: str | None = None,
        mode: str = "cheap",
        layers_per_block: int = 1,
    ):
        if not workers:
            raise ValueError("DistributedComputePlane needs at least one worker")

        has_high_bw = any(
            latency.get(a.worker_id, b.worker_id) < HIGH_BANDWIDTH_RTT_MS
            for a in workers
            for b in workers
            if a is not b
        )
        self.strategy = pick_strategy(
            is_moe=is_moe,
            has_high_bandwidth_tier=has_high_bw,
            explicit=explicit_strategy,
        )
        log.info("plane using strategy: %s", self.strategy.profile.name)

        self.geometry = geometry
        self.workers = list(workers)
        self.latency = latency
        self._units = self.strategy.build_unit_graph(
            geometry,
            layers_per_block=layers_per_block,
        )
        placement = self.strategy.plan(
            self._units,
            self.workers,
            self.latency,
            querier_account=querier_account,
            mode=mode,
        )
        if placement is None:
            raise RuntimeError("no feasible placement: pool too small for this model")
        self.placement: Placement = placement
        self._routing = _RoutingTable.from_placement(self.placement, self.workers)

    @property
    def virtual_resources(self) -> VirtualResources:
        backends_intersection: frozenset[str] = (
            frozenset.intersection(*(w.backends for w in self.workers))
            if self.workers
            else frozenset()
        )
        return VirtualResources(
            ram_gb_available=sum(w.ram_gb_free for w in self.workers),
            compute_tflops_per_sec=sum(w.compute_tflops_per_sec for w in self.workers),
            backends=backends_intersection,
            n_workers=len(self.workers),
        )

    async def reset_conversation(self, conv_id: str) -> None:
        log.debug("reset_conversation: distributed reset is a follow-up")

    async def shutdown(self) -> None:
        log.debug("shutdown: distributed teardown is a follow-up")

    async def generate(
        self,
        history: list[dict],
        *,
        max_tokens: int = 256,
        temperature: float = 0.7,
        on_token=None,
    ) -> tuple[str, GenerationStats]:
        raise NotImplementedError(
            "DistributedComputePlane.generate is scaffolded — worker-side "
            "compute_unit handler is the next PR. Use LocalComputePlane "
            "for self-utilization scenarios in the meantime."
        )

    def placement_summary(self) -> str:
        return " → ".join(f"{w}[{len(u)}u]" for w, u in self.placement.segments)

    def owner_of_unit(self, unit_id: str) -> ResourceDecl | None:
        return self._routing.owner_of(unit_id)

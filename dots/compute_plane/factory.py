"""Plane factory: pick the right plane for the workload.

Resolution order:
  1. `force_local` → LocalComputePlane.
  2. No workers supplied → LocalComputePlane.
  3. Geometry + latency given AND a feasible distributed placement
     exists → DistributedComputePlane.
  4. Otherwise → LocalComputePlane (graceful fallback).

The "fall back to local" branch is the self-utilization win: when the
pool can't help, don't pay the network tax.
"""
from __future__ import annotations

import logging
from typing import Sequence

from .base import ComputePlane
from .distributed import DistributedComputePlane
from .local import LocalComputePlane
from ..scheduler import LatencyMatrix, ResourceDecl
from ..work_units import ModelGeometry

log = logging.getLogger("dots.compute_plane.factory")


def create_plane(
    *,
    model_id: str,
    geometry: ModelGeometry | None = None,
    workers: Sequence[ResourceDecl] = (),
    latency: LatencyMatrix | None = None,
    querier_account: str | None = None,
    mode: str = "cheap",
    is_moe: bool = False,
    explicit_strategy: str | None = None,
    force_local: bool = False,
) -> ComputePlane:
    if force_local or not workers:
        return LocalComputePlane(model_id)
    if geometry is None or latency is None:
        return LocalComputePlane(model_id)
    try:
        plane = DistributedComputePlane(
            geometry=geometry,
            workers=workers,
            latency=latency,
            is_moe=is_moe,
            explicit_strategy=explicit_strategy,
            querier_account=querier_account,
            mode=mode,
        )
        log.info("plane: distributed (%d workers)", len(workers))
        return plane
    except RuntimeError:
        log.info("plane: no feasible distributed placement, falling back to local")
        return LocalComputePlane(model_id)

"""Plane factory: pick the right plane for the workload.

Resolution order:
  1. `force_local` -> LocalComputePlane.
  2. No workers supplied -> LocalComputePlane.
  3. Geometry + latency given AND a feasible distributed placement
     exists -> DistributedComputePlane.
  4. Otherwise fail clearly.

Local and distributed execution are explicit choices; the factory does
not hide distributed planning failures by silently loading locally.
"""

from __future__ import annotations

import logging
from typing import Sequence

from .base import ComputePlane, VirtualResources
from .distributed import DistributedComputePlane
from .local import LocalComputePlane
from ..backends.base import BackendRuntime
from ..placement.scheduler import LatencyMatrix, ResourceDecl
from ..placement.work_units import ModelGeometry

log = logging.getLogger("dots.compute_plane.factory")


def create_plane(
    *,
    local_runtime: BackendRuntime | None = None,
    local_resources: VirtualResources | None = None,
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
        return _local_plane(local_runtime, local_resources)
    if geometry is None or latency is None:
        return _local_plane(local_runtime, local_resources)
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
    except RuntimeError as e:
        raise RuntimeError(f"distributed plane unavailable: {e}") from e


def _local_plane(
    runtime: BackendRuntime | None,
    resources: VirtualResources | None,
) -> ComputePlane:
    if runtime is None or resources is None:
        raise RuntimeError(
            "local compute plane requires an already-loaded BackendRuntime "
            "and explicit VirtualResources"
        )
    return LocalComputePlane(runtime, resources)

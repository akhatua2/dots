"""Compute plane — the abstraction the model talks to.

A model running on dots sees a single virtual machine with composed
resources. The plane hides workers, network, and sharding strategy.

Public API:
  ComputePlane          abstract base
  LocalComputePlane     single machine, no network (self-utilization)
  DistributedComputePlane  multi-worker via scheduler + sharding strategy
  create_plane()        factory; falls back to local when distributed
                        isn't feasible
  VirtualResources      composed totals the plane offers
  GenerationStats       per-turn telemetry

Architectural position:
  application  →  compute_plane  →  sharding  →  scheduler  →  workers

The plane is the seam: application code never touches workers or
strategies directly.
"""
from __future__ import annotations

from .base import ComputePlane, GenerationStats, VirtualResources
from .distributed import DistributedComputePlane
from .factory import create_plane
from .local import LocalComputePlane

__all__ = [
    "ComputePlane",
    "LocalComputePlane",
    "DistributedComputePlane",
    "create_plane",
    "VirtualResources",
    "GenerationStats",
]

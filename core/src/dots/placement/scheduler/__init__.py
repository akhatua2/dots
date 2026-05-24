"""Resource-aware scheduling for the dots pool.

Public API re-exports. Internals split across:

  types       — dataclasses (ResourceDecl, WorkUnit, Placement, LatencyMatrix)
  enumerate   — feasible-placement enumeration
  select      — non-dominated filtering + user-mode picking
  api         — schedule() entry point + scoring

Contract: every placement returned by `schedule()` is non-dominated —
no feasible alternative does better on both CU AND TPS. Asserted in
tests/test_scheduler.py.
"""

from __future__ import annotations

from .api import schedule
from .enumerate import _segmentations, enumerate_placements
from .select import Mode, non_dominated_placements, pick_by_mode
from .types import LatencyMatrix, Placement, ResourceDecl, WorkUnit

__all__ = [
    "schedule",
    "enumerate_placements",
    "_segmentations",
    "Mode",
    "non_dominated_placements",
    "pick_by_mode",
    "LatencyMatrix",
    "Placement",
    "ResourceDecl",
    "WorkUnit",
]

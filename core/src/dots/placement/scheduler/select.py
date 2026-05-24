"""Select the best placement from a set of candidates.

Two steps:

  1. Drop placements that are strictly worse on both axes than another —
     we should never pay more CU AND get fewer TPS than an alternative.
     `non_dominated_placements()` does this.

  2. Among the survivors, pick one based on the user's preference mode
     (cheap / fast / balanced). `pick_by_mode()` does this.

The "non-dominated" framing is the user-facing guarantee: we will never
hand back a placement worse on both axes than something we could have
chosen instead.
"""

from __future__ import annotations

import math
from typing import Iterable, Literal, Sequence

from .types import Placement

DOMINANCE_EPSILON = 1e-6
DEFAULT_BALANCED_ALPHA = 0.5
NORMALIZATION_FLOOR = 1.0


Mode = Literal["cheap", "fast", "balanced"]


def non_dominated_placements(placements: Iterable[Placement]) -> list[Placement]:
    """Drop placements strictly worse on both axes than another.

    Placement A dominates B iff `A.cu_paid <= B.cu_paid` AND `A.tps >= B.tps`,
    with at least one strict inequality. Dominated placements are removed;
    the survivors form the boundary of "best achievable tradeoffs".
    """
    sorted_cands = sorted(placements, key=lambda p: (p.cu_paid, -p.tps))
    survivors: list[Placement] = []
    best_tps = -math.inf
    for p in sorted_cands:
        if p.tps > best_tps + DOMINANCE_EPSILON:
            survivors.append(p)
            best_tps = p.tps
    return survivors


def pick_by_mode(
    survivors: Sequence[Placement],
    mode: Mode = "cheap",
    balance_alpha: float = DEFAULT_BALANCED_ALPHA,
) -> Placement:
    """Pick one placement from the non-dominated set per user mode.

    cheap     → lowest cu_paid; ties broken by higher tps
    fast      → highest tps; ties broken by lower cu_paid
    balanced  → maximizes  alpha · normalized_tps  −  (1 − alpha) · normalized_cu
    """
    if not survivors:
        raise ValueError("no placements to pick from")
    if mode == "cheap":
        return min(survivors, key=lambda p: (p.cu_paid, -p.tps))
    if mode == "fast":
        return max(survivors, key=lambda p: (p.tps, -p.cu_paid))
    if mode == "balanced":
        cu_max = (
            max((p.cu_paid for p in survivors), default=NORMALIZATION_FLOOR)
            or NORMALIZATION_FLOOR
        )
        tps_max = (
            max((p.tps for p in survivors), default=NORMALIZATION_FLOOR)
            or NORMALIZATION_FLOOR
        )

        def util(p: Placement) -> float:
            return balance_alpha * (p.tps / tps_max) - (1 - balance_alpha) * (
                p.cu_paid / cu_max
            )

        return max(survivors, key=util)
    raise ValueError(f"unknown mode: {mode}")

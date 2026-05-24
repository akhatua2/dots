"""Expert-parallel sharding (EP) — scaffold only.

For MoE models: experts distributed across workers; per-token routing
sends each token to the workers holding its top-k experts. Not
implemented in v1 because:

  • ModelGeometry doesn't carry MoE facts yet (n_experts, top_k, gate dim).
  • Scheduler doesn't model the per-token routing probability that
    weighs each expert's expected FLOP load.
  • Worker-side routing primitive doesn't exist yet.

Once MoE checkpoints become a target, this class fleshes out and the
strategy registry's order means MoE detection automatically picks EP.
"""

from __future__ import annotations

from .base import ShardingStrategy, StrategyProfile


class ExpertParallel(ShardingStrategy):
    profile = StrategyProfile(
        name="ep",
        needs_high_bandwidth=False,
        needs_moe_model=True,
        works_across_clusters=True,
    )

    def build_unit_graph(self, geom, *, layers_per_block=1):
        raise NotImplementedError(
            "ExpertParallel is scaffolded; needs MoE-aware geometry + routing."
        )

    def plan(
        self,
        units,
        workers,
        latency,
        querier_account=None,
        mode="cheap",
        max_segments=None,
    ):
        raise NotImplementedError(
            "ExpertParallel is scaffolded; needs MoE-aware enumeration."
        )

"""Pipeline-parallel sharding (PP).

Each WorkUnit is assigned to exactly one worker; hidden states flow
through the chain. The only communication is per-unit forwarding —
kilobytes per hop. Works fine on consumer LAN; this is dots's default
and what we ship with v1.
"""
from __future__ import annotations

from .base import ShardingStrategy, StrategyProfile
from ..scheduler import schedule
from ..work_units import units_for_geometry


class PipelineParallel(ShardingStrategy):
    profile = StrategyProfile(
        name="pp",
        needs_high_bandwidth=False,
        needs_moe_model=False,
        works_across_clusters=True,
    )

    def build_unit_graph(self, geom, *, layers_per_block=1):
        return units_for_geometry(geom, layers_per_block=layers_per_block)

    def plan(self, units, workers, latency,
             querier_account=None, mode="cheap", max_segments=None):
        return schedule(
            units, workers, latency,
            querier_account=querier_account,
            mode=mode,
            max_segments=max_segments,
        )

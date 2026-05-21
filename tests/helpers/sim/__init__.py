"""Synthetic pool + model builders for tests and simulation.

Public API split across two modules:

  pool    — MacClass table, latency tier constants, worker builders,
            and small/multi-cluster/random pool factories.
  models  — ModelSpec dataclass, well-known model specs, unit-graph
            builder.

For real-model bridging (extracts geometry from a live MLX model) see
`dots.work_units` instead.
"""

from __future__ import annotations

from .models import (
    LLAMA_70B,
    QWEN_05B,
    QWEN_7B,
    ModelSpec,
    make_unit_graph,
)
from .pool import (
    LATENCY_LAN,
    LATENCY_OFFICE,
    LATENCY_OFFICE_TO_HOME,
    LATENCY_TAILNET,
    LATENCY_WAN,
    MAC_CLASSES,
    MacClass,
    make_multi_cluster_pool,
    make_random_pool,
    make_simple_pool,
    make_worker,
)

__all__ = [
    "MAC_CLASSES",
    "MacClass",
    "make_worker",
    "make_simple_pool",
    "make_multi_cluster_pool",
    "make_random_pool",
    "LATENCY_LAN",
    "LATENCY_OFFICE",
    "LATENCY_TAILNET",
    "LATENCY_OFFICE_TO_HOME",
    "LATENCY_WAN",
    "ModelSpec",
    "make_unit_graph",
    "QWEN_05B",
    "QWEN_7B",
    "LLAMA_70B",
]

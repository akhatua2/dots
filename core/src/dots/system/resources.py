"""Worker resource policy and discovery.

Workers advertise bounded resources to the hub. Defaults are conservative
percentages of current machine availability so model preparation and
loading do not silently claim the whole host.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..backends import registry
from .util import effective_tflops_estimate, free_ram_gb


@dataclass(frozen=True)
class WorkerResourceLimits:
    ram_gb: float
    disk_gb: float
    cpu_utilization: float
    io_read_gbps: float
    io_write_gbps: float
    compute_tflops_per_sec: float
    backends: frozenset[str]


DEFAULT_RAM_FRACTION = 0.75
DEFAULT_DISK_FRACTION = 0.50
DEFAULT_CPU_UTILIZATION = 0.50
DEFAULT_IO_READ_GBPS = 1.0
DEFAULT_IO_WRITE_GBPS = 0.5
BYTES_PER_GB = 1_000_000_000


def discover_worker_limits(
    *,
    max_ram_gb: float | None = None,
    cache_dir: str | os.PathLike | None = None,
) -> WorkerResourceLimits:
    ram_gb = free_ram_gb() * _float_env("DOTS_RAM_FRACTION", DEFAULT_RAM_FRACTION)
    if max_ram_gb is not None:
        ram_gb = min(ram_gb, float(max_ram_gb))

    disk_root = Path(cache_dir or os.environ.get("DOTS_CACHE_DIR") or ".")
    disk_free_gb = shutil.disk_usage(disk_root).free / BYTES_PER_GB
    disk_gb = disk_free_gb * _float_env("DOTS_DISK_FRACTION", DEFAULT_DISK_FRACTION)
    disk_cap = os.environ.get("DOTS_DISK_GB")
    if disk_cap:
        disk_gb = min(disk_gb, float(disk_cap))

    return WorkerResourceLimits(
        ram_gb=ram_gb,
        disk_gb=disk_gb,
        cpu_utilization=_float_env("DOTS_CPU_UTILIZATION", DEFAULT_CPU_UTILIZATION),
        io_read_gbps=_float_env("DOTS_IO_READ_GBPS", DEFAULT_IO_READ_GBPS),
        io_write_gbps=_float_env("DOTS_IO_WRITE_GBPS", DEFAULT_IO_WRITE_GBPS),
        compute_tflops_per_sec=effective_tflops_estimate(),
        backends=registry.available_backend_names(),
    )


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return float(raw)

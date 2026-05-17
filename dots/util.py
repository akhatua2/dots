"""Shared utilities."""
from __future__ import annotations

import subprocess


def free_ram_gb() -> float:
    """Approximate free RAM on macOS in gigabytes (free + inactive pages)."""
    out = subprocess.check_output(["vm_stat"]).decode()
    pages_free = pages_inactive = 0
    for line in out.splitlines():
        if line.startswith("Pages free:"):
            pages_free = int(line.split()[-1].rstrip("."))
        elif line.startswith("Pages inactive:"):
            pages_inactive = int(line.split()[-1].rstrip("."))
    return (pages_free + pages_inactive) * 16384 / (1024**3)

"""Backend artifact cache management.

Backends may create prepared artifacts such as converted weights. The
worker owns a disk budget; this cache manager reserves space and evicts
older artifacts before a backend writes new files.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CacheReservation:
    path: Path
    required_gb: float


class ModelCache:
    def __init__(self, root: str | Path, disk_limit_gb: float):
        self.root = Path(root)
        self.disk_limit_gb = float(disk_limit_gb)
        self.root.mkdir(parents=True, exist_ok=True)

    def reserve(self, key: str, required_gb: float) -> CacheReservation:
        if required_gb > self.disk_limit_gb:
            raise RuntimeError(
                f"model artifact needs {required_gb:.1f}GB disk, "
                f"worker limit is {self.disk_limit_gb:.1f}GB"
            )
        self._evict_until_available(required_gb)
        path = self.root / _safe_key(key)
        path.mkdir(parents=True, exist_ok=True)
        self.touch(path)
        return CacheReservation(path=path, required_gb=required_gb)

    def touch(self, path: str | Path) -> None:
        now = time.time()
        Path(path).touch(exist_ok=True)
        marker = Path(path) / ".last_used"
        marker.touch(exist_ok=True)
        os.utime(marker, (now, now))

    def bytes_used(self) -> int:
        total = 0
        for p in self.root.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
        return total

    def _evict_until_available(self, required_gb: float) -> None:
        required_bytes = int(required_gb * 1e9)
        limit_bytes = int(self.disk_limit_gb * 1e9)
        while self.bytes_used() + required_bytes > limit_bytes:
            victim = self._oldest_entry()
            if victim is None:
                break
            shutil.rmtree(victim)
        if self.bytes_used() + required_bytes > limit_bytes:
            raise RuntimeError(
                f"not enough cache disk after eviction: need {required_gb:.1f}GB, "
                f"limit is {self.disk_limit_gb:.1f}GB"
            )

    def _oldest_entry(self) -> Path | None:
        entries = [p for p in self.root.iterdir() if p.is_dir()]
        if not entries:
            return None

        def last_used(p: Path) -> float:
            marker = p / ".last_used"
            return marker.stat().st_mtime if marker.exists() else p.stat().st_mtime

        return min(entries, key=last_used)


def _safe_key(key: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in key)

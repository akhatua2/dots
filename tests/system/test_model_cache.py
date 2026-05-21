from __future__ import annotations

import os

import pytest

from dots.system.model_cache import ModelCache


def _cache_entry(cache: ModelCache, name: str, size_bytes: int, mtime: float) -> None:
    path = cache.root / name
    path.mkdir()
    (path / "blob").write_bytes(b"x" * size_bytes)
    marker = path / ".last_used"
    marker.touch()
    os.utime(marker, (mtime, mtime))


def test_model_cache_reserve_evicts_oldest_entry(tmp_path):
    cache = ModelCache(tmp_path, disk_limit_gb=0.000003)
    _cache_entry(cache, "old", 1_500, 10.0)
    _cache_entry(cache, "newer", 1_000, 20.0)

    reservation = cache.reserve("org/model", required_gb=0.000001)

    assert reservation.path == tmp_path / "org_model"
    assert not (tmp_path / "old").exists()
    assert (tmp_path / "newer").exists()


def test_model_cache_rejects_artifact_larger_than_worker_limit(tmp_path):
    cache = ModelCache(tmp_path, disk_limit_gb=1.0)

    with pytest.raises(RuntimeError, match="worker limit"):
        cache.reserve("too-large", required_gb=2.0)

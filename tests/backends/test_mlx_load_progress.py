"""Tests for dots.load_progress.

Covers the three pieces the progress bar depends on:
  • hf_cache_size_for — file-walking the HF cache directory
  • estimated_repo_total_size — HF API fallback behavior
  • monitor_download_progress — callback firing + final tick on cancel

The cache walker is monkeypatched onto a tmp directory so the tests
don't depend on actually downloading models.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dots.backends.mlx import load_progress


pytestmark = pytest.mark.asyncio


# ─────────────────────── hf_cache_size_for ───────────────────────────


def _setup_fake_cache(monkeypatch, tmp_path: Path, model_id: str) -> Path:
    """Point HF_HUB_CACHE at tmp_path and return the per-model dir."""
    monkeypatch.setattr(
        "huggingface_hub.constants.HF_HUB_CACHE",
        str(tmp_path),
    )
    model_dir = tmp_path / f"models--{model_id.replace('/', '--')}"
    model_dir.mkdir(parents=True)
    return model_dir


async def test_hf_cache_size_returns_zero_when_no_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "huggingface_hub.constants.HF_HUB_CACHE",
        str(tmp_path),
    )
    assert load_progress.hf_cache_size_for("nonexistent/model") == 0


async def test_hf_cache_size_sums_files(monkeypatch, tmp_path):
    model_dir = _setup_fake_cache(monkeypatch, tmp_path, "ns/m")
    blobs = model_dir / "blobs"
    blobs.mkdir()
    (blobs / "shard_a").write_bytes(b"x" * 1000)
    (blobs / "shard_b").write_bytes(b"y" * 2500)
    assert load_progress.hf_cache_size_for("ns/m") == 3500


async def test_hf_cache_size_includes_incomplete_partials(monkeypatch, tmp_path):
    """The progress bar would jump in big steps (waiting for full
    shards to land) if we didn't count `.incomplete` files. We DO
    count them — this test pins that behavior."""
    model_dir = _setup_fake_cache(monkeypatch, tmp_path, "ns/m")
    blobs = model_dir / "blobs"
    blobs.mkdir()
    (blobs / "finished").write_bytes(b"x" * 4000)
    (blobs / "in_flight.incomplete").write_bytes(b"y" * 1500)
    assert load_progress.hf_cache_size_for("ns/m") == 5500


async def test_hf_cache_size_walks_nested_dirs(monkeypatch, tmp_path):
    """HF's actual cache layout puts files under `blobs/` and symlinks
    them from `snapshots/`. The size walker has to recurse."""
    model_dir = _setup_fake_cache(monkeypatch, tmp_path, "ns/m")
    (model_dir / "blobs").mkdir()
    (model_dir / "snapshots" / "abc123").mkdir(parents=True)
    (model_dir / "blobs" / "f1").write_bytes(b"a" * 100)
    (model_dir / "snapshots" / "abc123" / "config.json").write_bytes(b"{}" * 5)
    assert load_progress.hf_cache_size_for("ns/m") == 100 + 10


# ─────────────────────── estimated_repo_total_size ─────────────────


async def test_estimated_size_returns_zero_on_api_failure(monkeypatch):
    """Offline / rate-limited / unknown-model cases return 0 so the chat
    UI can fall back to an indefinite spinner gracefully."""

    class _BoomApi:
        def repo_info(self, *_a, **_kw):
            raise RuntimeError("network down")

    monkeypatch.setattr("huggingface_hub.HfApi", _BoomApi)
    assert load_progress.estimated_repo_total_size("ns/m") == 0


async def test_estimated_size_sums_safetensors_only(monkeypatch):
    """Tokenizer / config files exist but only safetensors count toward
    the total — they're the bulk of the download."""

    class _Sibling:
        def __init__(self, name, size):
            self.rfilename = name
            self.size = size

    class _Info:
        siblings = [
            _Sibling("config.json", 4 * 1024),
            _Sibling("tokenizer.json", 8 * 1024),
            _Sibling("model-00001-of-00002.safetensors", 9_000_000_000),
            _Sibling("model-00002-of-00002.safetensors", 9_000_000_000),
        ]

    class _Api:
        def repo_info(self, *_a, **_kw):
            return _Info()

    monkeypatch.setattr("huggingface_hub.HfApi", _Api)
    assert load_progress.estimated_repo_total_size("ns/m") == 18_000_000_000


# ─────────────────────── monitor_download_progress ─────────────────


async def test_monitor_emits_callbacks_periodically(monkeypatch):
    """Monitor calls the callback on each tick until cancelled."""
    monkeypatch.setattr(
        load_progress,
        "estimated_repo_total_size",
        lambda _m: 100,
    )
    monkeypatch.setattr(load_progress, "xet_chunk_cache_size", lambda: 0)
    calls = []
    sizes = iter([10, 25, 50, 80])

    def _size(_):
        try:
            return next(sizes)
        except StopIteration:
            return 80

    monkeypatch.setattr(load_progress, "hf_cache_size_for", _size)

    async def cb(bytes_now, bytes_total):
        calls.append((bytes_now, bytes_total))

    task = asyncio.create_task(
        load_progress.monitor_download_progress("ns/m", cb, interval_seconds=0.01)
    )
    await asyncio.sleep(0.06)  # let several ticks happen
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(calls) >= 3, calls
    # Every call carries the same bytes_total.
    assert {c[1] for c in calls} == {100}


async def test_monitor_emits_final_tick_on_cancel(monkeypatch):
    """Cancellation triggers one more callback so the UI sees the
    completed state (e.g. 100%) rather than freezing on the last poll."""
    monkeypatch.setattr(
        load_progress,
        "estimated_repo_total_size",
        lambda _m: 100,
    )
    monkeypatch.setattr(load_progress, "xet_chunk_cache_size", lambda: 0)
    sizes = iter([10, 20])

    def _size(_):
        try:
            return next(sizes)
        except StopIteration:
            return 100  # "finished" — what the post-cancel tick should see

    monkeypatch.setattr(load_progress, "hf_cache_size_for", _size)

    calls = []

    async def cb(now, total):
        calls.append(now)

    task = asyncio.create_task(
        load_progress.monitor_download_progress("ns/m", cb, interval_seconds=0.01)
    )
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert 100 in calls, f"final tick should have seen the completed size; got {calls}"


async def test_monitor_counts_xet_chunk_delta(monkeypatch):
    """Xet-backed downloads stage chunks in `~/.cache/huggingface/xet/`
    long before any file lands in `hub/blobs/`. The monitor must count
    the in-flight Xet bytes (delta from the pre-download baseline) so
    the bar doesn't appear frozen while Xet is busy. This was the
    actual user-facing bug: dots saw 22 MB on the bar while hf_xet
    had already transferred 2+ GB into the chunk-cache."""
    monkeypatch.setattr(
        load_progress,
        "estimated_repo_total_size",
        lambda _m: 10_000,
    )
    # hub/blobs/ stays at 0 — Xet hasn't reconstructed any files yet.
    monkeypatch.setattr(load_progress, "hf_cache_size_for", lambda _m: 0)
    # Xet cache starts at 50 MB worth of unrelated chunks from earlier
    # downloads; during this download it grows.
    xet_sizes = iter(
        [
            50_000,  # baseline (taken before first tick)
            50_000 + 2_000,  # +2k of new chunks
            50_000 + 5_500,  # +5.5k
            50_000 + 8_000,  # +8k
        ]
    )
    monkeypatch.setattr(
        load_progress,
        "xet_chunk_cache_size",
        lambda: next(xet_sizes, 50_000 + 8_000),
    )

    seen = []

    async def cb(now, total):
        seen.append(now)

    task = asyncio.create_task(
        load_progress.monitor_download_progress("ns/m", cb, interval_seconds=0.01)
    )
    await asyncio.sleep(0.06)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # bar should reflect the in-flight Xet delta, not stay at 0.
    assert max(seen) >= 5_500, f"monitor missed Xet chunk-cache bytes; saw {seen}"
    # baseline subtracted out — never includes the pre-existing 50 MB
    # of unrelated chunks.
    assert max(seen) < 50_000, f"monitor counted pre-baseline Xet chunks; saw {seen}"


async def test_monitor_progress_is_monotonic(monkeypatch):
    """When Xet completes a file and evicts its chunks, the Xet cache
    can shrink mid-download. The bar must not go backwards — UX has
    to be monotonic. We clamp via a high-water mark."""
    monkeypatch.setattr(
        load_progress,
        "estimated_repo_total_size",
        lambda _m: 1_000_000,
    )
    # hub/blobs/ jumps up when reconstruction completes.
    hub_sizes = iter([0, 0, 100_000, 100_000])
    monkeypatch.setattr(
        load_progress,
        "hf_cache_size_for",
        lambda _m: next(hub_sizes, 100_000),
    )
    # Xet cache grows then shrinks (eviction after reconstruction).
    xet_sizes = iter([0, 80_000, 90_000, 10_000])
    monkeypatch.setattr(
        load_progress,
        "xet_chunk_cache_size",
        lambda: next(xet_sizes, 10_000),
    )

    seen = []

    async def cb(now, total):
        seen.append(now)

    task = asyncio.create_task(
        load_progress.monitor_download_progress("ns/m", cb, interval_seconds=0.01)
    )
    await asyncio.sleep(0.06)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Each successive value should be >= the prior one.
    for prev, nxt in zip(seen, seen[1:]):
        assert nxt >= prev, f"progress went backwards: {seen}"


async def test_monitor_clamps_to_total(monkeypatch):
    """The combined hub + Xet delta can briefly exceed the API-reported
    total because the API only sums safetensors but the on-disk cache
    also has config / tokenizer files. Clamp at 100% so the bar
    doesn't display 103%."""
    monkeypatch.setattr(
        load_progress,
        "estimated_repo_total_size",
        lambda _m: 1_000,
    )
    monkeypatch.setattr(load_progress, "hf_cache_size_for", lambda _m: 1_500)
    monkeypatch.setattr(load_progress, "xet_chunk_cache_size", lambda: 0)

    seen = []

    async def cb(now, total):
        seen.append(now)

    task = asyncio.create_task(
        load_progress.monitor_download_progress("ns/m", cb, interval_seconds=0.01)
    )
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert all(v <= 1_000 for v in seen), f"bytes_now exceeded bytes_total; saw {seen}"


async def test_monitor_survives_callback_errors(monkeypatch):
    """A misbehaving callback shouldn't kill the monitor — the next
    tick still runs."""
    monkeypatch.setattr(
        load_progress,
        "estimated_repo_total_size",
        lambda _m: 100,
    )
    monkeypatch.setattr(load_progress, "xet_chunk_cache_size", lambda: 0)
    monkeypatch.setattr(load_progress, "hf_cache_size_for", lambda _m: 50)
    seen = []

    async def cb(now, total):
        seen.append(now)
        raise RuntimeError("ui blew up")

    task = asyncio.create_task(
        load_progress.monitor_download_progress("ns/m", cb, interval_seconds=0.01)
    )
    await asyncio.sleep(0.04)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert len(seen) >= 2, seen  # didn't stop after the first error

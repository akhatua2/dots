"""Track how much of a model has been downloaded so the chat client
can show a real progress bar instead of an indefinite spinner.

The download itself is what takes minutes during a first-time
`load_units`. We surface progress by polling two on-disk locations
the HuggingFace stack writes into:

  * `~/.cache/huggingface/hub/models--<repo>/` — the assembled cache.
    `.incomplete` shards grow here as legacy LFS downloads stream;
    Xet downloads only land here on file-reconstruction completion.
  * `~/.cache/huggingface/xet/chunk-cache/` — where `hf_xet` stages
    deduplicated chunks during transfer. For Xet-backed repos (the
    common case for the mlx-community models we use), the bulk of
    download bytes appear here long before any file is reconstructed
    into the assembled cache.

We measure both, with a per-run baseline on the Xet cache so chunks
from earlier unrelated downloads don't inflate the bar.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger("dots.load_progress")


def hf_cache_size_for(model_id: str) -> int:
    """Bytes on disk in the HF cache for this model — including
    in-flight `.incomplete` partial downloads.

    Including `.incomplete` bytes is important for a smooth progress
    bar: HF downloads shards into `<name>.safetensors.incomplete` and
    renames on completion, so a multi-GB shard contributes zero to a
    "completed files only" view for the full duration of its download.
    Counting partial bytes lets the bar grow steadily during each
    shard rather than jumping in big steps every few minutes.
    """
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
    except ImportError:
        return 0
    model_dir = Path(HF_HUB_CACHE) / f"models--{model_id.replace('/', '--')}"
    if not model_dir.exists():
        return 0
    total = 0
    for path in model_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def xet_chunk_cache_size() -> int:
    """Total bytes staged in the Xet chunk-cache across all repos.

    `hf_xet` writes deduplicated chunks here during transfer; the
    file in `hub/blobs/` only grows on file-reconstruction completion.
    So during a multi-GB Xet-backed download, this directory is where
    most of the download bytes live. Callers should snapshot this
    once at start of a download and subtract to get the in-flight
    delta for the active model.
    """
    cache = Path.home() / ".cache" / "huggingface" / "xet" / "chunk-cache"
    if not cache.exists():
        return 0
    total = 0
    for path in cache.rglob("*"):
        if not path.is_file():
            continue
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def estimated_repo_total_size(model_id: str) -> int:
    """Best-effort total bytes the worker needs to download.

    Walks the repo's file list via the HF API and sums safetensors
    sizes (the bulk of the download). Returns 0 on failure so the chat
    UI can fall back to an indefinite spinner.
    """
    try:
        from huggingface_hub import HfApi

        info = HfApi().repo_info(model_id, files_metadata=True)
        total = 0
        for sib in info.siblings:
            if sib.rfilename.endswith(".safetensors") and sib.size:
                total += sib.size
        return total
    except Exception as e:
        log.debug("could not estimate repo size for %s: %s", model_id, e)
        return 0


ProgressCallback = Callable[[int, int], Awaitable[None]]


def _measure_bytes_now(model_id: str, xet_baseline: int, high_water: int) -> int:
    """Combine assembled-cache bytes with the in-flight Xet chunk delta,
    clamped monotonically by `high_water` so the bar never goes
    backward when Xet evicts chunks after file reconstruction.
    """
    hub_bytes = hf_cache_size_for(model_id)
    xet_now = xet_chunk_cache_size()
    xet_delta = max(0, xet_now - xet_baseline)
    return max(high_water, hub_bytes + xet_delta)


async def monitor_download_progress(
    model_id: str,
    callback: ProgressCallback,
    interval_seconds: float = 0.6,
) -> None:
    """Until cancelled: every `interval_seconds`, measure how many bytes
    of `model_id` are on disk (assembled cache + in-flight Xet chunks)
    and emit `(bytes_now, bytes_total)` through `callback`.
    Cancellation is the normal stop signal (raised when load completes).
    """
    bytes_total = await asyncio.to_thread(estimated_repo_total_size, model_id)
    xet_baseline = await asyncio.to_thread(xet_chunk_cache_size)
    high_water = 0
    try:
        while True:
            bytes_now = await asyncio.to_thread(
                _measure_bytes_now,
                model_id,
                xet_baseline,
                high_water,
            )
            if bytes_total:
                bytes_now = min(bytes_now, bytes_total)
            high_water = bytes_now
            try:
                await callback(bytes_now, bytes_total)
            except Exception:
                log.debug("progress callback raised", exc_info=True)
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        # Final tick so the UI sees 100% when load finishes (rather than
        # stopping at whatever the last poll caught).
        try:
            final_now = await asyncio.to_thread(
                _measure_bytes_now,
                model_id,
                xet_baseline,
                high_water,
            )
            if bytes_total:
                final_now = min(final_now, bytes_total)
            await callback(final_now, bytes_total)
        except Exception:
            pass
        raise

"""Track how much of a model has been downloaded so the chat client
can show a real progress bar instead of an indefinite spinner.

The download itself is what takes minutes during a first-time
`load_units`. We surface progress by polling the HuggingFace cache
directory for `model_id` and comparing total bytes-on-disk to the
expected total (queried once at start via the HF API).
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


async def monitor_download_progress(
    model_id: str,
    callback: ProgressCallback,
    interval_seconds: float = 0.6,
) -> None:
    """Until cancelled: every `interval_seconds`, measure how many bytes
    of `model_id` are on disk and emit `(bytes_now, bytes_total)`
    through `callback`. Cancellation is the normal stop signal (raised
    when load completes).
    """
    bytes_total = await asyncio.to_thread(estimated_repo_total_size, model_id)
    try:
        while True:
            bytes_now = await asyncio.to_thread(hf_cache_size_for, model_id)
            try:
                await callback(bytes_now, bytes_total)
            except Exception:
                log.debug("progress callback raised", exc_info=True)
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        # Final tick so the UI sees 100% when load finishes (rather than
        # stopping at whatever the last poll caught).
        try:
            final_now = await asyncio.to_thread(hf_cache_size_for, model_id)
            await callback(final_now, bytes_total)
        except Exception:
            pass
        raise

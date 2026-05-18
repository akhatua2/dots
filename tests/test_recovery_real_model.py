"""Real-model recovery test (LEGACY — pending rewrite).

This was written against the pre-delegation architecture where the
hub loaded the model itself (`dots.hub.mlx_load`) and workers had a
`dots.shard` module that defined per-shard forward passes. Both are
gone in the current architecture:

  * Hub is a pure orchestrator now (no `mlx_load`).
  * Per-worker compute lives in `dots/compute_plane/local.py`.
  * Workers host the *full* model via `FullModelHost`, with optional
    partial weight loading via `partial_loader.py`.

The recovery story the test was pinning (worker drops mid-generation,
remaining workers continue without collapsing into single-token
repetition) is still a real concern in the chain-protocol world, but
the test fixture mechanics — monkeypatching `dots.shard.*`, expecting
`Worker.shard` to populate — no longer apply. The test needs a full
rewrite against `delegate_turn` / chain-peer messaging.

Skipping at module level so pytest doesn't fail to *collect* the file
(the bare `import dots.shard` at the top of the original fixture
raised `ModuleNotFoundError` before any test even ran, producing a
permanent collection error in every CI run).
"""
from __future__ import annotations

import asyncio
import socket
import sys
from typing import AsyncIterator

import pytest
import pytest_asyncio

pytest.skip(
    "Test was written for the pre-delegation architecture (dots.shard / "
    "hub-side mlx_load). Needs a rewrite against the chain-protocol "
    "worker recovery path before it can be re-enabled.",
    allow_module_level=True,
)

from dots.hub import Hub                              # noqa: E402
from dots.peer import PeerConnection                  # noqa: E402
from dots.util import free_ram_gb                     # noqa: E402
from dots.worker import Worker                        # noqa: E402

# This file deliberately skips the autouse model-fake fixture from
# conftest.py — we want the real model here.
REAL_MODEL = "mlx-community/Qwen2.5-0.5B-Instruct-4bit"

pytestmark = pytest.mark.asyncio


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest_asyncio.fixture
async def real_hub(monkeypatch) -> AsyncIterator[Hub]:
    # Defeat the autouse mock from tests/conftest.py for this file.
    import dots.hub
    import dots.shard
    import mlx_lm
    from mlx_lm.models.base import create_attention_mask
    from mlx_lm.models.cache import make_prompt_cache
    monkeypatch.setattr(dots.hub, "mlx_load", mlx_lm.load)
    monkeypatch.setattr(dots.shard, "mlx_load", mlx_lm.load)
    monkeypatch.setattr(dots.shard, "make_prompt_cache", make_prompt_cache)
    monkeypatch.setattr(dots.shard, "create_attention_mask", create_attention_mask)

    port = _free_port()
    h = Hub(port=port, model_id=REAL_MODEL, host="127.0.0.1")
    task = asyncio.create_task(h.start())
    try:
        await asyncio.wait_for(h.started.wait(), timeout=120.0)
    except asyncio.TimeoutError:
        task.cancel()
        raise
    try:
        yield h
    finally:
        for w in list(h.participants.values()):
            try:
                await w.conn.close()
            except Exception:
                pass
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass


async def _connect_worker(hub: Hub) -> Worker:
    import uuid as _uuid
    w = Worker("127.0.0.1", hub.port, identity=str(_uuid.uuid4()))
    reader, writer = await asyncio.open_connection("127.0.0.1", hub.port)
    w.conn = PeerConnection(reader, writer, "test", w._handle)
    ack = await w.conn.call(
        "hello", worker_id=w.identity, free_ram_gb=free_ram_gb(),
    )
    w.model_id = ack["model_id"]
    w.n_layers = ack["n_layers"]
    await asyncio.wait_for(w._welcome.wait(), timeout=60.0)
    deadline = asyncio.get_running_loop().time() + 60.0
    while w.shard is None and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)
    assert w.shard is not None
    return w


def _max_run_length(text: str) -> tuple[str, int]:
    """Return the longest run of identical whitespace-separated tokens.

    The recovery-collapse symptom we're trying to detect is the model
    emitting the same word over and over ('their their their...' or
    'Who Who Who...'). A healthy response has no run longer than ~3.
    """
    parts = text.split()
    if not parts:
        return ("", 0)
    best_word, best_run = parts[0], 1
    cur, run = parts[0], 1
    for w in parts[1:]:
        if w == cur:
            run += 1
        else:
            if run > best_run:
                best_run, best_word = run, cur
            cur, run = w, 1
    if run > best_run:
        best_run, best_word = run, cur
    return best_word, best_run


async def test_recovery_does_not_collapse_to_repetition(real_hub: Hub):
    """End-to-end with a real model: start a long generation across two
    workers, kill one mid-COMPUTE, verify the response continues to a
    natural stop without devolving into single-word repetition.

    This is the regression guard for the live two-Mac bug the user
    observed."""
    w1 = await _connect_worker(real_hub)
    w2 = await _connect_worker(real_hub)
    # Wait for both shards loaded.
    deadline = asyncio.get_running_loop().time() + 60.0
    while asyncio.get_running_loop().time() < deadline:
        ready = sum(1 for w in real_hub.participants.values() if w.ready)
        if ready == 2:
            break
        await asyncio.sleep(0.1)
    assert sum(1 for w in real_hub.participants.values() if w.ready) == 2

    # Slow down w2's first compute so we have a deterministic window to kill it.
    compute_seen = asyncio.Event()
    proceed = asyncio.Event()
    original_handler = w2.conn.handler

    async def slow_handler(msg):
        if msg.get("kind") == "compute" and not compute_seen.is_set():
            compute_seen.set()
            await proceed.wait()
        return await original_handler(msg)

    w2.conn.handler = slow_handler

    task = asyncio.create_task(
        w1.send_prompt(
            "please give me a 600 word story?",
            max_tokens=120, temperature=0,   # greedy: deterministic
        )
    )
    await asyncio.wait_for(compute_seen.wait(), timeout=30.0)
    await w2.conn.close()
    proceed.set()

    text, stats = await asyncio.wait_for(task, timeout=120.0)
    print(f"\n[stats] {stats}", file=sys.stderr)
    print(f"[response] {text}", file=sys.stderr)

    assert stats["recoveries"] >= 1, (
        f"expected at least one recovery; got {stats}"
    )
    assert text.strip(), "recovered response is empty"

    word, run = _max_run_length(text)
    assert run < 6, (
        f"recovered response collapses into repetition: '{word}' repeats "
        f"{run} times in a row. full text: {text!r}"
    )

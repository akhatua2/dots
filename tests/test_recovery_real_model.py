"""Real-model recovery test.

This is the only test that loads an actual mlx-lm model (Qwen2.5-0.5B,
~300 MB cached after first run). It's the missing link between the fast
fake-backed tests and what the user actually experiences: it verifies
that after a worker drops mid-generation, the recovery produces a
*coherent* continuation rather than collapsing into single-token
repetition (the bug pattern the user observed in production:
'their their their their…').

Marked slow so it's opt-in:

    .venv/bin/python -m pytest tests/test_recovery_real_model.py -v

It does NOT run with the default `pytest tests/` invocation, which
intentionally stays under a second.
"""
from __future__ import annotations

import asyncio
import socket
import sys
from typing import AsyncIterator

import pytest
import pytest_asyncio

from dots.hub import Hub
from dots.peer import PeerConnection
from dots.util import free_ram_gb
from dots.worker import Worker

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
    w = Worker("127.0.0.1", hub.port)
    reader, writer = await asyncio.open_connection("127.0.0.1", hub.port)
    w.conn = PeerConnection(reader, writer, "test", w._handle)
    ack = await w.conn.call("hello", free_ram_gb=free_ram_gb())
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

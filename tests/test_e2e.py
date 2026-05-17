"""End-to-end tests that exercise dots through its public interfaces only.

Each test verifies one architectural property:

  • test_chat_works                 — full pipeline (hub + worker, prompt → tokens)
  • test_layers_split_across_workers — re-plan distributes layers when N workers join
  • test_kv_cache_persists_across_turns — second turn's prefill is much smaller
  • test_recovery_when_worker_drops — generation continues after a worker disconnect

Backed by fakes (see fakes.py + conftest.py) so the whole suite runs in well
under a second.
"""
from __future__ import annotations

import asyncio

import pytest

from dots.hub import Hub
from .conftest import connect_worker, wait_for_pool_size
from .fakes import EOS_TOKEN, DEFAULT_LAYERS


pytestmark = pytest.mark.asyncio


async def test_chat_works(hub: Hub):
    """Hub + one worker + one prompt → a non-empty token stream and stats
    that reflect a real generation having happened."""
    worker = await connect_worker(hub)
    await wait_for_pool_size(hub, 1)
    assert worker.shard.spec.layer_start == 0
    assert worker.shard.spec.layer_end == hub.n_layers

    text, stats = await worker.send_prompt("hello", max_tokens=4, temperature=0)
    assert stats["decode_tokens"] == 4, stats          # hit max_tokens
    assert stats["prefill_tokens"] > 0, stats
    assert stats["recoveries"] == 0, stats
    assert text, f"empty stream: {text!r}"


async def test_layers_split_across_workers(hub: Hub):
    """With two workers connected, the planner should give each a contiguous
    non-empty range and the ranges should cover every layer exactly once."""
    await connect_worker(hub)
    await connect_worker(hub)
    await wait_for_pool_size(hub, 2)

    workers = sorted(hub.participants.values(), key=lambda w: w.id)
    r1, r2 = workers[0].layer_range, workers[1].layer_range
    assert r1[0] == 0
    assert r1[1] == r2[0]
    assert r2[1] == hub.n_layers == DEFAULT_LAYERS
    assert r1[1] > r1[0] and r2[1] > r2[0]   # both non-empty


async def test_kv_cache_persists_across_turns(hub: Hub):
    """The same conversation across two turns: the second prefill should be
    much smaller than the first because the prior tokens are already in
    every shard's KV cache."""
    worker = await connect_worker(hub)
    await wait_for_pool_size(hub, 1)

    _, s1 = await worker.send_prompt(
        "first message with some content", max_tokens=3, temperature=0,
    )
    _, s2 = await worker.send_prompt("hi", max_tokens=3, temperature=0)

    assert s1["prefill_tokens"] > s2["prefill_tokens"], (
        f"expected second prefill to be smaller (cache reuse), got "
        f"{s1['prefill_tokens']} → {s2['prefill_tokens']}"
    )
    # The second prefill should only cover the new user message + the
    # new assistant-start marker (a handful of tokens). If it's bigger
    # than the first message's text, cache reuse is broken.
    assert s2["prefill_tokens"] <= len("hi") + 5, s2

    # The shard's KV cache should now contain everything from both turns —
    # the first user message, first response, second user message, second
    # response, plus terminators.
    cache = worker.shard._caches[worker.conv_id]
    assert cache[0].len > 0
    # Every layer in our range should agree on cache length.
    cache_lens = {c.len for c in cache}
    assert len(cache_lens) == 1, f"layer caches diverged: {cache_lens}"


async def test_recovery_when_worker_drops(hub: Hub):
    """Kick off a generation across two workers, drop one *during* its first
    compute call, and verify the generation still completes with at least
    one recovery. We synchronize on a sentinel event in w2's handler so the
    kill lands inside the in-flight window deterministically — without it,
    the fake layers run so fast that the turn finishes before we can race
    in a disconnect."""
    w1 = await connect_worker(hub)
    w2 = await connect_worker(hub)
    await wait_for_pool_size(hub, 2)

    compute_seen = asyncio.Event()
    proceed = asyncio.Event()
    original_handler = w2.conn.handler

    async def slow_handler(msg):
        if msg.get("kind") == "compute" and not compute_seen.is_set():
            compute_seen.set()
            # Hold here until the test kills us — gives the hub a real
            # in-flight COMPUTE that will raise ConnectionError on close.
            await proceed.wait()
        return await original_handler(msg)

    w2.conn.handler = slow_handler

    prompt_task = asyncio.create_task(
        w1.send_prompt("test prompt", max_tokens=8, temperature=0)
    )
    await asyncio.wait_for(compute_seen.wait(), timeout=2.0)
    await w2.conn.close()
    proceed.set()  # let the hung handler unwind cleanly (it'll fail to send)

    text, stats = await asyncio.wait_for(prompt_task, timeout=5.0)
    assert text, "recovered turn produced nothing"
    assert stats["decode_tokens"] > 0
    assert stats["recoveries"] >= 1, stats

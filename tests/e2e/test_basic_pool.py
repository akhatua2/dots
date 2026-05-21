"""End-to-end tests that exercise dots through its public interfaces only.

Each test verifies one architectural property:

  • test_chat_works                 — full pipeline (hub + worker, prompt → tokens)
  • test_layers_split_across_workers — re-plan distributes layers when N workers join
  • test_kv_cache_persists_across_turns — second turn's prefill is much smaller
  • test_delegate_worker_drop_rolls_back_user_turn — failed turns do not
    corrupt conversation history

Backed by fakes (see fakes.py + conftest.py) so the whole suite runs in well
under a second.
"""

from __future__ import annotations

import pytest

from dots.hub import Hub
from tests.conftest import connect_worker, wait_for_pool_size


pytestmark = pytest.mark.asyncio


async def test_chat_works(hub: Hub):
    """Hub + one worker + one prompt → a non-empty token stream and stats
    that reflect a real delegated generation having happened."""
    worker = await connect_worker(hub)
    await wait_for_pool_size(hub, 1)
    # In the delegation architecture every compute worker hosts the
    # whole model. Its FullModelHost should be loaded by the time
    # `connect_worker` returns.
    assert worker.host is not None
    assert worker.host.n_layers == hub.n_layers

    text, stats = await worker.send_prompt("hello", max_tokens=4, temperature=0)
    assert stats["decode_tokens"] == 4, stats  # hit max_tokens
    assert stats["prefill_tokens"] > 0, stats
    assert text, f"empty stream: {text!r}"


async def test_two_workers_both_ready(hub: Hub):
    """With two workers connected, both load the full model and become
    ready. (Replaces the legacy layer-split assertion — workers no longer
    own layer ranges; they host the whole model.)"""
    await connect_worker(hub)
    await connect_worker(hub)
    await wait_for_pool_size(hub, 2)

    workers = sorted(hub.participants.values(), key=lambda w: w.id)
    for w in workers:
        assert w.ready
        assert w.layer_range == (0, hub.n_layers)


async def test_kv_cache_persists_across_turns(hub: Hub):
    """Two turns on one conversation: the worker's FullModelHost keeps its
    per-conv KV cache, so the second prefill stays small."""
    worker = await connect_worker(hub)
    await wait_for_pool_size(hub, 1)

    _, s1 = await worker.send_prompt(
        "first message with some content",
        max_tokens=3,
        temperature=0,
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


async def test_delegate_worker_drop_rolls_back_user_turn(hub: Hub):
    """Current delegation behavior: a worker drop fails the turn cleanly
    and removes the in-flight user message from conversation history."""
    worker = await connect_worker(hub)
    await wait_for_pool_size(hub, 1)

    participant = hub.participants[worker.identity]
    original_call = participant.conn.call

    async def drop_on_delegate(kind: str, **payload):
        if kind == "delegate_turn":
            raise ConnectionError("synthetic worker drop")
        return await original_call(kind, **payload)

    participant.conn.call = drop_on_delegate

    with pytest.raises(RuntimeError, match="worker dropped mid-turn"):
        await worker.send_prompt("this turn should fail", max_tokens=4)

    assert participant.conv.history == []

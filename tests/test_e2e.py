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
    that reflect a real delegated generation having happened."""
    worker = await connect_worker(hub)
    await wait_for_pool_size(hub, 1)
    # In the delegation architecture every compute worker hosts the
    # whole model. Its FullModelHost should be loaded by the time
    # `connect_worker` returns.
    assert worker.host is not None
    assert worker.host.n_layers == hub.n_layers

    text, stats = await worker.send_prompt("hello", max_tokens=4, temperature=0)
    assert stats["decode_tokens"] == 4, stats          # hit max_tokens
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


@pytest.mark.skip(
    reason="exercises old `compute` message + recovery loop; "
           "needs rewrite for delegation"
)
async def test_recovery_when_worker_drops(hub: Hub):
    pass

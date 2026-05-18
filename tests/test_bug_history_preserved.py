"""Reproduces the 'history wiped under in-flight turn' bug.

The user observed: after a worker drops mid-generation, the recovered
response collapses to single-token repetition, AND the next turn's
prefill is huge (~300 tokens for a 2-character input). The theory
is that `_on_pool_changed` clears `conv.history` while a turn is in
flight, dropping the just-appended user message — the recovery then
re-prefills WITHOUT the user's question.
"""
from __future__ import annotations

import asyncio
import pytest

from dots.hub import Hub
from tests.conftest import connect_worker, wait_for_pool_size


pytestmark = pytest.mark.asyncio


@pytest.mark.skip(reason="exercises the old `compute` message + recovery loop; "
                         "needs a rewrite for the delegate_turn architecture")
async def test_user_message_survives_worker_drop(hub: Hub):
    """If a worker drops mid-turn, the user's message must still be in
    conv.history when the turn finishes. (Currently fails — this is the
    bug.)"""
    w1 = await connect_worker(hub)
    w2 = await connect_worker(hub)
    await wait_for_pool_size(hub, 2)

    user_msg = "what color is the sky?"

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
        w1.send_prompt(user_msg, max_tokens=8, temperature=0)
    )
    await asyncio.wait_for(compute_seen.wait(), timeout=2.0)
    await w2.conn.close()
    proceed.set()

    _, stats = await asyncio.wait_for(task, timeout=5.0)
    assert stats["recoveries"] >= 1, "test setup broken — no recovery exercised"

    # Find w1's conversation on the hub side.
    convs = [p.conv for p in hub.participants.values()
             if p.conv.history]   # whichever still has history
    assert len(convs) >= 1, "no conversation has any history after turn"
    conv = convs[0]
    user_turns = [m for m in conv.history if m.get("role") == "user"]
    assert user_turns, (
        f"BUG: user message lost from history after recovery. "
        f"History after recovery: {conv.history}"
    )
    assert any(user_msg in m["content"] for m in user_turns), (
        f"BUG: expected user msg in history, got {user_turns}"
    )

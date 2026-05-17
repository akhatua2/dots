"""End-to-end test of the ledger hooked into the hub.

Verifies that a successful turn produces a ledger entry with the correct
zero-sum credit/debit attribution.
"""
from __future__ import annotations

import asyncio

import pytest

from dots.hub import Hub
from .conftest import connect_worker, wait_for_pool_size


pytestmark = pytest.mark.asyncio


async def test_turn_records_ledger_entry(hub: Hub):
    """A successful turn should produce exactly one turn entry, with the
    querier debited and workers credited summing to the same amount."""
    worker = await connect_worker(hub)
    await wait_for_pool_size(hub, 1)

    assert len(hub.ledger.turns) == 0

    text, stats = await worker.send_prompt(
        "hello", max_tokens=4, temperature=0,
    )
    assert text
    assert len(hub.ledger.turns) == 1

    entry = hub.ledger.turns[0]
    entry.verify_zero_sum()
    assert entry.querier_account == worker.account_id
    # One worker, single account → credits map has exactly one entry
    # equal to total spent.
    assert entry.credited == {worker.account_id: entry.spent}
    # spent should equal total_layers × total_tokens
    expected_total_tokens = stats["prefill_tokens"] + stats["decode_tokens"]
    assert entry.spent == hub.n_layers * expected_total_tokens


async def test_pool_sum_zero_after_solo_turns(hub: Hub):
    """After many solo turns (one account is the only worker AND the
    querier), the account's balance must be exactly zero."""
    worker = await connect_worker(hub)
    await wait_for_pool_size(hub, 1)
    for _ in range(5):
        await worker.send_prompt("hi", max_tokens=2, temperature=0)
    acc = hub.ledger.account(worker.account_id)
    assert acc is not None
    assert acc.balance == 0
    assert hub.ledger.pool_sum() == 0

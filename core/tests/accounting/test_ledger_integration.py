"""End-to-end test of the ledger hooked into the hub.

The hub fixture in conftest uses the fake-model fixtures from fakes.py.
Those fakes don't expose real `parameters()`, so `Hub.active_params` is
0 and `_attribute_turn` correctly skips ledger recording.

To exercise the actual record_turn path through the hub, we patch in
a synthetic non-zero `active_params` after the hub starts.
"""

from __future__ import annotations

import pytest

from dots.distributed.hub import Hub
from tests.conftest import connect_worker, wait_for_pool_size


pytestmark = pytest.mark.asyncio


SYNTH_ACTIVE_PARAMS = 7_000_000_000  # pretend it's a 7B model


async def test_no_ledger_entry_without_active_params(hub: Hub):
    """If Hub.active_params is 0 (fake model in tests), _attribute_turn
    skips recording so the ledger stays empty. This is the documented
    fallback for test fakes."""
    worker = await connect_worker(hub)
    await wait_for_pool_size(hub, 1)
    assert hub.active_params == 0
    text, _stats = await worker.send_prompt("hi", max_tokens=2, temperature=0)
    assert text
    # Ledger should be empty — fakes don't have a FLOP rate.
    assert hub.ledger.turns == []


async def test_turn_records_ledger_entry_with_synthetic_params(hub: Hub):
    """Patch in a non-zero active_params and confirm a real ledger entry
    is produced with the FLOP-CU math."""
    hub.active_params = SYNTH_ACTIVE_PARAMS
    worker = await connect_worker(hub)
    await wait_for_pool_size(hub, 1)
    assert len(hub.ledger.turns) == 0

    text, stats = await worker.send_prompt(
        "hello",
        max_tokens=4,
        temperature=0,
    )
    assert text
    assert len(hub.ledger.turns) == 1

    entry = hub.ledger.turns[0]
    entry.verify_zero_sum()
    assert entry.querier_account == worker.account_id
    assert entry.active_params == SYNTH_ACTIVE_PARAMS
    assert entry.model_id == hub.model_id

    total_tokens = stats["prefill_tokens"] + stats["decode_tokens"]
    expected_flops = 2 * SYNTH_ACTIVE_PARAMS * total_tokens
    assert entry.flops_done == expected_flops
    # Solo worker = one credit slot covering all layers.
    assert entry.credited == {worker.account_id: entry.spent}


async def test_pool_sum_zero_after_solo_turns(hub: Hub):
    """After many turns where the same account is both querier and only
    worker, the pool sum must be exactly zero."""
    hub.active_params = SYNTH_ACTIVE_PARAMS
    worker = await connect_worker(hub)
    await wait_for_pool_size(hub, 1)
    for _ in range(5):
        await worker.send_prompt("hi", max_tokens=2, temperature=0)
    acc = hub.ledger.account(worker.account_id)
    assert acc is not None
    assert acc.balance == 0
    assert hub.ledger.pool_sum() == 0

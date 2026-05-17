"""Ledger correctness.

The central invariant: for every recorded turn, `spent == sum(credited)`.
And at the pool level, the sum of every account's balance is always
exactly zero — what one account spent another account earned.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from dots.ledger import Ledger


# ───────────────────────────── helpers ───────────────────────────────────


def random_plan(rng: random.Random, n_workers: int, n_layers: int
                ) -> list[tuple[str, str | None, int]]:
    """Generate a plan with `n_workers` accounts that sums to `n_layers`
    total. Multiple slots may share an account (multi-mac single-user)."""
    # Start by giving each slot one layer, then distribute the remainder.
    if n_workers > n_layers:
        n_workers = n_layers
    sizes = [1] * n_workers
    for _ in range(n_layers - n_workers):
        sizes[rng.randrange(n_workers)] += 1

    # Pick a small set of accounts so multi-slot-per-account happens often.
    n_accounts = max(1, rng.randint(1, n_workers))
    accounts = [f"acc-test-{i}" for i in range(n_accounts)]
    plan = []
    for sz in sizes:
        acc = accounts[rng.randrange(n_accounts)]
        plan.append((acc, None, sz))
    return plan


# ──────────────────────────── unit tests ─────────────────────────────────


def test_record_turn_zero_sum(tmp_path: Path):
    led = Ledger(tmp_path / "credits.json")
    plan = [("acc-A", None, 5), ("acc-B", None, 7), ("acc-C", None, 3)]
    entry = led.record_turn(
        conv_id="conv1", turn_idx=1,
        querier_account="acc-arpan", querier_alias="arpan",
        plan=plan, prefill_tokens=100, decode_tokens=50, total_layers=15,
    )
    assert entry.spent == 15 * 150
    assert sum(entry.credited.values()) == entry.spent
    entry.verify_zero_sum()


def test_pool_sum_always_zero(tmp_path: Path):
    led = Ledger(tmp_path / "credits.json")
    led.record_turn(
        conv_id="c", turn_idx=1,
        querier_account="acc-Q", querier_alias=None,
        plan=[("acc-A", None, 4), ("acc-B", None, 6)],
        prefill_tokens=10, decode_tokens=20, total_layers=10,
    )
    led.record_turn(
        conv_id="c", turn_idx=2,
        querier_account="acc-B", querier_alias=None,
        plan=[("acc-A", None, 7), ("acc-Q", None, 3)],
        prefill_tokens=5, decode_tokens=15, total_layers=10,
    )
    assert led.pool_sum() == 0


def test_solo_pool_nets_to_zero(tmp_path: Path):
    """If a single account is both the querier and the only worker, its
    net change per turn is exactly zero."""
    led = Ledger(tmp_path / "credits.json")
    for i in range(10):
        led.record_turn(
            conv_id="solo", turn_idx=i,
            querier_account="acc-solo", querier_alias="solo",
            plan=[("acc-solo", None, 24)],
            prefill_tokens=20, decode_tokens=30, total_layers=24,
        )
    assert led.accounts["acc-solo"].balance == 0


def test_multi_account_balance_flows(tmp_path: Path):
    """arpan queries; some compute is on a friend's worker → arpan goes
    negative by exactly what friend gains."""
    led = Ledger(tmp_path / "credits.json")
    led.record_turn(
        conv_id="c", turn_idx=1,
        querier_account="acc-arpan", querier_alias="arpan",
        plan=[("acc-arpan", None, 20), ("acc-friend", "friend", 4)],
        prefill_tokens=50, decode_tokens=10, total_layers=24,
    )
    arpan = led.accounts["acc-arpan"]
    friend = led.accounts["acc-friend"]
    # 4 layers × 60 tokens = 240 CU paid by arpan to friend.
    assert friend.balance == 4 * 60
    assert arpan.balance == -4 * 60
    assert arpan.balance + friend.balance == 0


def test_persistence_round_trip(tmp_path: Path):
    path = tmp_path / "credits.json"
    led = Ledger(path)
    led.record_turn(
        conv_id="x", turn_idx=1,
        querier_account="acc-Q", querier_alias=None,
        plan=[("acc-A", "alice", 10)],
        prefill_tokens=5, decode_tokens=5, total_layers=10,
    )
    assert path.exists()

    led2 = Ledger(path)
    assert "acc-A" in led2.accounts
    assert led2.accounts["acc-A"].balance == 100
    assert led2.accounts["acc-A"].alias == "alice"
    assert led2.accounts["acc-Q"].balance == -100
    assert led2.pool_sum() == 0
    assert len(led2.turns) == 1


def test_persistence_atomic_against_partial_write(tmp_path: Path):
    """If a write fails partway, the existing ledger file isn't corrupted.

    Simulated by writing one good ledger, then injecting a write failure
    on the next record_turn — the existing file should still be valid.
    """
    path = tmp_path / "credits.json"
    led = Ledger(path)
    led.record_turn(
        conv_id="x", turn_idx=1, querier_account="acc-Q", querier_alias=None,
        plan=[("acc-A", None, 5)],
        prefill_tokens=10, decode_tokens=10, total_layers=5,
    )
    before = path.read_text()

    # Monkey-patch the flush to raise mid-write.
    orig = led._flush
    def boom():
        raise OSError("simulated disk full")
    led._flush = boom
    with pytest.raises(OSError):
        led.record_turn(
            conv_id="x", turn_idx=2, querier_account="acc-Q",
            querier_alias=None, plan=[("acc-A", None, 5)],
            prefill_tokens=1, decode_tokens=1, total_layers=5,
        )
    led._flush = orig

    # File on disk is unchanged.
    assert path.read_text() == before


def test_random_turns_maintain_zero_sum(tmp_path: Path):
    """Fuzz test: many random turns, conservation must hold forever."""
    rng = random.Random(42)
    led = Ledger(tmp_path / "credits.json")
    for i in range(200):
        n_workers = rng.randint(1, 5)
        n_layers = rng.randint(n_workers, 40)
        plan = random_plan(rng, n_workers, n_layers)
        querier = f"acc-querier-{rng.randint(0, 4)}"
        prefill = rng.randint(0, 100)
        decode = rng.randint(0, 100)
        if prefill + decode == 0:
            continue
        led.record_turn(
            conv_id=f"conv-{rng.randint(0, 3)}", turn_idx=i,
            querier_account=querier, querier_alias=None, plan=plan,
            prefill_tokens=prefill, decode_tokens=decode,
            total_layers=n_layers,
        )
        # Pool sum must always be zero.
        assert led.pool_sum() == 0, f"pool drift at turn {i}"


def test_load_rejects_tampered_entry(tmp_path: Path):
    """A ledger file with a turn entry whose credits don't sum to spent
    is detected on load (via verify_zero_sum)."""
    path = tmp_path / "credits.json"
    # Write a deliberately bad ledger.
    bad = {
        "version": 1,
        "accounts": {"acc-A": {
            "account_id": "acc-A", "alias": None,
            "earned_lifetime": 100, "spent_lifetime": 0,
            "turns_as_querier": 0, "turns_as_worker": 1,
            "first_seen_iso": "2026-01-01T00:00:00+00:00",
            "last_active_iso": "2026-01-01T00:00:00+00:00",
        }},
        "turns": [{
            "started_iso": "2026-01-01T00:00:00+00:00",
            "conv_id": "x", "turn_idx": 1,
            "querier_account": "acc-Q",
            "total_layers": 10, "prefill_tokens": 5, "decode_tokens": 5,
            "spent": 100,
            "credited": {"acc-A": 999},  # tampered: doesn't match spent
        }],
    }
    path.write_text(json.dumps(bad))
    with pytest.raises(AssertionError):
        Ledger(path)


def test_alias_updates_on_change(tmp_path: Path):
    led = Ledger(tmp_path / "credits.json")
    led.record_turn(
        conv_id="x", turn_idx=1, querier_account="acc-Q", querier_alias=None,
        plan=[("acc-A", "old-name", 5)],
        prefill_tokens=1, decode_tokens=1, total_layers=5,
    )
    assert led.accounts["acc-A"].alias == "old-name"
    led.record_turn(
        conv_id="x", turn_idx=2, querier_account="acc-Q", querier_alias=None,
        plan=[("acc-A", "new-name", 5)],
        prefill_tokens=1, decode_tokens=1, total_layers=5,
    )
    assert led.accounts["acc-A"].alias == "new-name"

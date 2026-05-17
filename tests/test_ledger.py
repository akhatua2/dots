"""Ledger correctness under the FLOP-CU formula.

The central invariant: for every recorded turn,
    spent (μCU) == sum(credited.values())  (exact integer)

And at the pool level: sum of every account's balance is always 0.

1 CU = 1 TFLOP. Stored as μCU (1 μCU = 10⁶ FLOPs).
flops_done = 2 × active_params × tokens.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from dots.ledger import Ledger, flops_to_microcu


# Test model dimensions used throughout. Picked so the FLOP numbers are
# nontrivial without being obnoxious.
QWEN7B_PARAMS = 7_000_000_000           # 7B
QWEN05B_PARAMS = 500_000_000            # 500M


# ───────────────────────────── helpers ───────────────────────────────────


def random_plan(rng: random.Random, n_workers: int, n_layers: int
                ) -> list[tuple[str, str | None, int]]:
    """Generate a plan with `n_workers` slots summing to `n_layers` layers.
    Multiple slots may share an account (the multi-mac single-user case)."""
    if n_workers > n_layers:
        n_workers = n_layers
    sizes = [1] * n_workers
    for _ in range(n_layers - n_workers):
        sizes[rng.randrange(n_workers)] += 1

    n_accounts = max(1, rng.randint(1, n_workers))
    accounts = [f"acc-test-{i}" for i in range(n_accounts)]
    plan = []
    for sz in sizes:
        acc = accounts[rng.randrange(n_accounts)]
        plan.append((acc, None, sz))
    return plan


# ──────────────────────────── unit tests ─────────────────────────────────


def test_flops_to_microcu_truncates():
    # 1 μCU = 1e6 FLOPs. Sub-μCU FLOPs are truncated (no rounding).
    assert flops_to_microcu(0) == 0
    assert flops_to_microcu(999_999) == 0
    assert flops_to_microcu(1_000_000) == 1
    assert flops_to_microcu(7_500_000) == 7
    assert flops_to_microcu(10**12) == 10**6  # 1 TFLOP = 1e6 μCU = 1 CU


def test_record_turn_zero_sum(tmp_path: Path):
    led = Ledger(tmp_path / "credits.json")
    plan = [("acc-A", None, 5), ("acc-B", None, 7), ("acc-C", None, 3)]
    entry = led.record_turn(
        conv_id="conv1", turn_idx=1,
        querier_account="acc-arpan", querier_alias="arpan",
        plan=plan, prefill_tokens=100, decode_tokens=50, total_layers=15,
        model_id="test/qwen", active_params=QWEN7B_PARAMS,
    )
    expected_flops = 2 * QWEN7B_PARAMS * 150
    assert entry.flops_done == expected_flops
    assert entry.spent == flops_to_microcu(expected_flops)
    assert sum(entry.credited.values()) == entry.spent
    entry.verify_zero_sum()


def test_pool_sum_always_zero(tmp_path: Path):
    led = Ledger(tmp_path / "credits.json")
    led.record_turn(
        conv_id="c", turn_idx=1,
        querier_account="acc-Q", querier_alias=None,
        plan=[("acc-A", None, 4), ("acc-B", None, 6)],
        prefill_tokens=10, decode_tokens=20, total_layers=10,
        model_id="m", active_params=QWEN7B_PARAMS,
    )
    led.record_turn(
        conv_id="c", turn_idx=2,
        querier_account="acc-B", querier_alias=None,
        plan=[("acc-A", None, 7), ("acc-Q", None, 3)],
        prefill_tokens=5, decode_tokens=15, total_layers=10,
        model_id="m", active_params=QWEN7B_PARAMS,
    )
    assert led.pool_sum() == 0


def test_solo_pool_nets_to_zero(tmp_path: Path):
    """One account is both querier and sole worker — every turn must
    net to exactly zero regardless of how rounding falls."""
    led = Ledger(tmp_path / "credits.json")
    for i in range(10):
        led.record_turn(
            conv_id="solo", turn_idx=i,
            querier_account="acc-solo", querier_alias="solo",
            plan=[("acc-solo", None, 24)],
            prefill_tokens=20, decode_tokens=30, total_layers=24,
            model_id="m", active_params=QWEN05B_PARAMS,
        )
    assert led.accounts["acc-solo"].balance == 0


def test_multi_account_balance_flows(tmp_path: Path):
    """arpan queries; some compute is on a friend's worker → arpan
    goes negative by exactly what friend gains."""
    led = Ledger(tmp_path / "credits.json")
    led.record_turn(
        conv_id="c", turn_idx=1,
        querier_account="acc-arpan", querier_alias="arpan",
        plan=[("acc-arpan", None, 20), ("acc-friend", "friend", 4)],
        prefill_tokens=50, decode_tokens=10, total_layers=24,
        model_id="m", active_params=QWEN7B_PARAMS,
    )
    arpan = led.accounts["acc-arpan"]
    friend = led.accounts["acc-friend"]
    # friend held 4/24 of the model; arpan paid the full amount but got
    # 20/24 back. Difference is exactly the 4/24 friend earned.
    assert friend.balance == -arpan.balance
    assert arpan.balance + friend.balance == 0


def test_cu_scales_with_active_params(tmp_path: Path):
    """Same tokens × layers configuration, different model size →
    proportionally different CU. Verifies model-aware fairness."""
    led = Ledger(tmp_path / "credits.json")
    for model_idx, params in enumerate([QWEN05B_PARAMS, QWEN7B_PARAMS]):
        led.record_turn(
            conv_id=f"c{model_idx}", turn_idx=1,
            querier_account=f"acc-Q{model_idx}", querier_alias=None,
            plan=[(f"acc-W{model_idx}", None, 10)],
            prefill_tokens=50, decode_tokens=50, total_layers=10,
            model_id=f"m{model_idx}", active_params=params,
        )
    small_spent = led.turns[0].spent
    big_spent = led.turns[1].spent
    # Qwen-7B is ~14× more params than Qwen-0.5B, so big turn should
    # cost ~14× more CU.
    assert big_spent == 14 * small_spent


def test_persistence_round_trip(tmp_path: Path):
    path = tmp_path / "credits.json"
    led = Ledger(path)
    led.record_turn(
        conv_id="x", turn_idx=1,
        querier_account="acc-Q", querier_alias=None,
        plan=[("acc-A", "alice", 10)],
        prefill_tokens=5, decode_tokens=5, total_layers=10,
        model_id="qwen-7b", active_params=QWEN7B_PARAMS,
    )
    assert path.exists()

    led2 = Ledger(path)
    assert "acc-A" in led2.accounts
    assert led2.accounts["acc-A"].alias == "alice"
    assert led2.accounts["acc-Q"].balance == -led2.accounts["acc-A"].balance
    assert led2.pool_sum() == 0
    assert len(led2.turns) == 1
    # Persistence should preserve model context.
    assert led2.turns[0].model_id == "qwen-7b"
    assert led2.turns[0].active_params == QWEN7B_PARAMS


def test_persistence_atomic_against_partial_write(tmp_path: Path):
    """If a write fails partway through, the existing ledger file is
    not corrupted."""
    path = tmp_path / "credits.json"
    led = Ledger(path)
    led.record_turn(
        conv_id="x", turn_idx=1, querier_account="acc-Q", querier_alias=None,
        plan=[("acc-A", None, 5)],
        prefill_tokens=10, decode_tokens=10, total_layers=5,
        model_id="m", active_params=QWEN7B_PARAMS,
    )
    before = path.read_text()

    def boom():
        raise OSError("simulated disk full")
    led._flush = boom
    with pytest.raises(OSError):
        led.record_turn(
            conv_id="x", turn_idx=2, querier_account="acc-Q",
            querier_alias=None, plan=[("acc-A", None, 5)],
            prefill_tokens=1, decode_tokens=1, total_layers=5,
            model_id="m", active_params=QWEN7B_PARAMS,
        )

    assert path.read_text() == before


def test_random_turns_maintain_zero_sum(tmp_path: Path):
    """Fuzz test: many random turns across many model sizes. Conservation
    must hold for every turn AND across the whole pool."""
    rng = random.Random(42)
    led = Ledger(tmp_path / "credits.json")
    candidate_params = [QWEN05B_PARAMS, QWEN7B_PARAMS, 32_000_000_000]
    for i in range(200):
        n_workers = rng.randint(1, 5)
        n_layers = rng.randint(n_workers, 40)
        plan = random_plan(rng, n_workers, n_layers)
        querier = f"acc-querier-{rng.randint(0, 4)}"
        prefill = rng.randint(0, 100)
        decode = rng.randint(0, 100)
        if prefill + decode == 0:
            continue
        active = candidate_params[rng.randrange(len(candidate_params))]
        led.record_turn(
            conv_id=f"conv-{rng.randint(0, 3)}", turn_idx=i,
            querier_account=querier, querier_alias=None, plan=plan,
            prefill_tokens=prefill, decode_tokens=decode,
            total_layers=n_layers,
            model_id=f"model-{active}", active_params=active,
        )
        assert led.pool_sum() == 0, f"pool drift at turn {i}"


def test_load_rejects_tampered_entry(tmp_path: Path):
    """A ledger file with credits that don't sum to spent is rejected
    on load (the AssertionError in verify_zero_sum fires)."""
    path = tmp_path / "credits.json"
    bad = {
        "version": 3,
        "unit": "1 CU = 1 TFLOP",
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
            "model_id": "m", "active_params": QWEN7B_PARAMS,
            "total_layers": 10, "prefill_tokens": 5, "decode_tokens": 5,
            "flops_done": 100,
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
        model_id="m", active_params=QWEN7B_PARAMS,
    )
    assert led.accounts["acc-A"].alias == "old-name"
    led.record_turn(
        conv_id="x", turn_idx=2, querier_account="acc-Q", querier_alias=None,
        plan=[("acc-A", "new-name", 5)],
        prefill_tokens=1, decode_tokens=1, total_layers=5,
        model_id="m", active_params=QWEN7B_PARAMS,
    )
    assert led.accounts["acc-A"].alias == "new-name"


def test_old_version_resets(tmp_path: Path):
    """A ledger file from an older version (e.g. when we used token-cents)
    is silently discarded and the ledger starts fresh."""
    path = tmp_path / "credits.json"
    path.write_text(json.dumps({
        "version": 2,  # an older version
        "accounts": {},
        "turns": [],
    }))
    led = Ledger(path)
    assert led.accounts == {}
    assert led.turns == []

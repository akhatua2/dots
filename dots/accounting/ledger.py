"""Closed compute-unit (CU) ledger.

**1 CU = 1 trillion floating-point operations (1 TFLOP).**

This is a physical unit, not a token count or a layer count. It works
identically across models, quantizations, hardware, and frameworks —
a TFLOP done on an M1 Pro is the same CU as a TFLOP done on an H100.

Per-turn formula:

    flops_done   = 2 × active_params × total_tokens
                  (active_params from the model the turn ran on;
                   2× from forward-pass multiply-add accounting)

    spent_by_querier   = flops_done  (in CU)
    earned_by_worker_i = flops_done × layers_owned_i / total_layers
                       (each worker is paid its share of the model)

    sum_i(earned_by_worker_i) == spent_by_querier      (zero-sum)

To preserve exact zero-sum under fractional layer shares, everything is
stored internally as integer **μCU** (1 μCU = 10⁻⁶ CU = 10⁶ FLOPs).
Display divides by 10⁶.

The hub is the single source of truth. It records every credit and debit
atomically to ~/.dots/credits.json. The ledger has no separate write
authority — workers and chat clients can READ the public state via
`GET /v1/credits`, but they cannot write.

**The ledger is append-only by design.** There is no `record_turn_undo`,
no `delete_account`, no `dots credits reset` subcommand. Once a turn is
recorded, it stays recorded forever. This is load-bearing for:
  - audit-trail credibility ([[cryptographic-ledger]] depends on it)
  - debt non-repudiation (you can't wipe a negative balance)
  - cross-pool balance transfers (a forgeable history is worthless)

If the file gets corrupted (version mismatch, JSON parse error), `_load`
silently discards it and starts fresh. That's a *recovery* path, not a
feature. During development you can `rm ~/.dots/credits.json` out of
band; in any real deployment that should be considered destructive and
controlled like any other production-data deletion.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

log = logging.getLogger("dots.ledger")


# 1 CU = 10⁶ μCU. 1 μCU = 10⁶ FLOPs. So 1 CU = 1 TFLOP.
MICROCU_PER_CU = 1_000_000
FLOPS_PER_MICROCU = 1_000_000


def flops_to_microcu(flops: int) -> int:
    """Convert raw FLOPs to integer μCU (truncated)."""
    return flops // FLOPS_PER_MICROCU


def microcu_to_cu(microcu: int) -> float:
    """Convert μCU to display CU."""
    return microcu / MICROCU_PER_CU


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Account:
    """Per-account aggregate state. All amounts are in μCU (integer)."""

    account_id: str
    alias: str | None = None
    earned_lifetime: int = 0  # μCU
    spent_lifetime: int = 0  # μCU
    turns_as_querier: int = 0
    turns_as_worker: int = 0
    first_seen_iso: str = field(default_factory=_now_iso)
    last_active_iso: str = field(default_factory=_now_iso)

    @property
    def balance(self) -> int:
        """Net balance in μCU. Convert to CU by dividing by 10⁶."""
        return self.earned_lifetime - self.spent_lifetime

    @property
    def balance_cu(self) -> float:
        return microcu_to_cu(self.balance)

    @property
    def earned_cu(self) -> float:
        return microcu_to_cu(self.earned_lifetime)

    @property
    def spent_cu(self) -> float:
        return microcu_to_cu(self.spent_lifetime)


@dataclass
class TurnEntry:
    """A single audit-log entry. Each entry is fully self-describing:
    it includes the model_id and active_params used, so anyone with the
    log can independently verify the CU math without consulting any
    external registry."""

    started_iso: str
    conv_id: str
    turn_idx: int
    querier_account: str
    model_id: str
    active_params: int
    total_layers: int
    prefill_tokens: int
    decode_tokens: int
    flops_done: int  # 2 × active_params × total_tokens
    spent: int  # μCU; should equal flops_to_microcu(flops_done)
    credited: dict[str, int]  # account_id -> μCU

    def verify_zero_sum(self) -> None:
        total_credited = sum(self.credited.values())
        if total_credited != self.spent:
            raise AssertionError(
                f"zero-sum violated for turn "
                f"{self.conv_id}:{self.turn_idx}: "
                f"spent={self.spent} but credits sum to {total_credited}"
            )


class Ledger:
    """In-memory account state + append-only turn log, persisted as JSON.

    All stored amounts are in *μCU* (1 μCU = 10⁻⁶ CU = 10⁶ FLOPs).
    Display layers convert to CU by dividing by 10⁶.
    """

    # Bump on any storage-format change.
    #   v1: layer-tokens unit (huge numbers)
    #   v2: tokens unit (cents)
    #   v3: FLOP unit (μCU)
    VERSION = 3

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.accounts: dict[str, Account] = {}
        self.turns: list[TurnEntry] = []
        if path.exists():
            self._load()

    # ──────────────────────────────── public ────────────────────────────

    def record_turn(
        self,
        *,
        conv_id: str,
        turn_idx: int,
        querier_account: str,
        querier_alias: str | None,
        plan: list[tuple[str, str | None, int]],
        prefill_tokens: int,
        decode_tokens: int,
        total_layers: int,
        model_id: str,
        active_params: int,
    ) -> TurnEntry:
        """Atomically credit each worker in `plan` and debit the querier.

        Inputs:
          plan          — [(account_id, alias_or_None, layers_owned)] for
                          every active worker that participated.
          total_layers  — sum of layers_owned across the plan.
          active_params — model's active parameter count (used in the
                          FLOP calculation; for MoE models this is the
                          per-token active set, not total params).

        Raises if the conservation invariant doesn't hold (shouldn't
        happen with the formula below; the assertion catches programmer
        error).
        """
        total_tokens = prefill_tokens + decode_tokens
        # FLOP accounting: 2× for forward-pass multiply-add.
        flops_done = 2 * active_params * total_tokens
        spent = flops_to_microcu(flops_done)

        # Distribute the spent amount to workers proportional to their
        # layer share. Integer math with remainder distribution so the
        # per-turn sum is exact.
        credited: dict[str, int] = {}
        remainder = spent
        for acc_id, _alias, layers_owned in plan:
            share = (spent * layers_owned) // total_layers
            credited[acc_id] = credited.get(acc_id, 0) + share
            remainder -= share
        # Distribute the leftover μCU one-at-a-time in plan order.
        # Each worker gets at most +1 extra μCU; deterministic.
        i = 0
        while remainder > 0 and plan:
            acc_id, _alias, _layers = plan[i % len(plan)]
            credited[acc_id] += 1
            remainder -= 1
            i += 1

        # Upsert account records (so aliases get updated even for
        # zero-credit accounts in the plan).
        for acc_id, alias, _layers_owned in plan:
            self._upsert_account(acc_id, alias)

        # Apply earnings — one turn-counted bump per unique account,
        # even if the account holds multiple slots in the plan.
        for acc_id, amount in credited.items():
            account = self.accounts[acc_id]
            account.earned_lifetime += amount
            account.turns_as_worker += 1
            account.last_active_iso = _now_iso()

        # Apply debit.
        querier = self._upsert_account(querier_account, querier_alias)
        querier.spent_lifetime += spent
        querier.turns_as_querier += 1
        querier.last_active_iso = _now_iso()

        entry = TurnEntry(
            started_iso=_now_iso(),
            conv_id=conv_id,
            turn_idx=turn_idx,
            querier_account=querier_account,
            model_id=model_id,
            active_params=active_params,
            total_layers=total_layers,
            prefill_tokens=prefill_tokens,
            decode_tokens=decode_tokens,
            flops_done=flops_done,
            spent=spent,
            credited=credited,
        )
        entry.verify_zero_sum()
        self.turns.append(entry)
        self._flush()
        return entry

    def account(self, account_id: str) -> Account | None:
        return self.accounts.get(account_id)

    def pool_sum(self) -> int:
        """Sum of balances across all accounts, in μCU. Must always be 0."""
        return sum(a.balance for a in self.accounts.values())

    def as_dict(self) -> dict:
        """JSON-serializable snapshot for the /v1/credits API.
        Includes both raw μCU and display-friendly CU."""
        return {
            "version": self.VERSION,
            "unit": "1 CU = 1 TFLOP = 1e12 FLOPs (stored as μCU = 1e6 FLOPs)",
            "accounts": {
                aid: {
                    **asdict(a),
                    "balance": a.balance,
                    "balance_cu": a.balance_cu,
                    "earned_cu": a.earned_cu,
                    "spent_cu": a.spent_cu,
                }
                for aid, a in self.accounts.items()
            },
            "turns": [
                {**asdict(t), "spent_cu": microcu_to_cu(t.spent)} for t in self.turns
            ],
        }

    # ──────────────────────────── persistence ───────────────────────────

    def _upsert_account(self, account_id: str, alias: str | None) -> Account:
        acc = self.accounts.get(account_id)
        if acc is None:
            acc = Account(account_id=account_id, alias=alias)
            self.accounts[account_id] = acc
        elif alias and acc.alias != alias:
            acc.alias = alias
        return acc

    def _flush(self) -> None:
        """Atomic write: temp file in the same directory, fsync, rename."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.as_dict(), indent=2, sort_keys=False)
        fd, tmp = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=".credits.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log.warning(
                "could not load ledger from %s (%s); starting fresh", self.path, e
            )
            return
        if raw.get("version") != self.VERSION:
            log.warning(
                "ledger version mismatch (file=%s, code=%s); starting fresh",
                raw.get("version"),
                self.VERSION,
            )
            return
        for aid, raw_acc in raw.get("accounts", {}).items():
            # Drop computed/display-only fields that may not match.
            for k in ("balance", "balance_cu", "earned_cu", "spent_cu"):
                raw_acc.pop(k, None)
            self.accounts[aid] = Account(**raw_acc)
        for raw_turn in raw.get("turns", []):
            raw_turn.pop("spent_cu", None)  # display-only
            entry = TurnEntry(**raw_turn)
            entry.verify_zero_sum()
            self.turns.append(entry)

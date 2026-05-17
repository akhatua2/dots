"""Closed credit-unit (CU) ledger.

The economic invariant: every CU spent by a querier must equal the sum of
CU credited to participating workers — *per turn*. This is enforced both
when entries are appended (assertion) and exposed to tests as a property.

CU formula for one turn:

    earned_by_worker_i = (layer_end_i - layer_start_i) * (prefill + decode)
    spent_by_querier   = total_layers                  * (prefill + decode)
                       = sum_i(earned_by_worker_i)

The hub is the single source of truth. It records every credit and debit
atomically to ~/.dots/credits.json. The ledger has no separate write
authority — workers and chat clients can READ the public state via
`GET /v1/credits`, but they cannot write.
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Account:
    """Aggregate state for a single account_id. Computed from the turn log
    on load; updated incrementally on every credit/debit."""
    account_id: str
    alias: str | None = None
    earned_lifetime: int = 0
    spent_lifetime: int = 0
    turns_as_querier: int = 0
    turns_as_worker: int = 0
    first_seen_iso: str = field(default_factory=_now_iso)
    last_active_iso: str = field(default_factory=_now_iso)

    @property
    def balance(self) -> int:
        return self.earned_lifetime - self.spent_lifetime


@dataclass
class TurnEntry:
    """A single audit-log entry. Records who paid, who got paid, how much.

    The invariant `spent == sum(credited.values())` is asserted both when
    the entry is created (via `Ledger.record_turn`) and when the ledger is
    loaded from disk.
    """
    started_iso: str
    conv_id: str
    turn_idx: int
    querier_account: str
    total_layers: int
    prefill_tokens: int
    decode_tokens: int
    spent: int
    credited: dict[str, int]

    def verify_zero_sum(self) -> None:
        total_credited = sum(self.credited.values())
        if total_credited != self.spent:
            raise AssertionError(
                f"zero-sum violated for turn {self.conv_id}:{self.turn_idx}: "
                f"spent={self.spent} but credits sum to {total_credited}"
            )


class Ledger:
    """In-memory account state + append-only turn log, persisted as JSON."""

    VERSION = 1

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
    ) -> TurnEntry:
        """Atomically credit each worker in `plan` and debit the querier.

        `plan` is a list of `(worker_account_id, worker_alias_or_None,
        layers_owned)` for every active worker that participated. Aliases
        update the account label if changed.

        Raises if the conservation invariant doesn't hold (which would
        mean a bug in the caller — should never happen with the formula
        below).
        """
        total_tokens = prefill_tokens + decode_tokens
        credited: dict[str, int] = {}
        for acc_id, alias, layers_owned in plan:
            credited[acc_id] = credited.get(acc_id, 0) + layers_owned * total_tokens
            account = self._upsert_account(acc_id, alias)
            account.earned_lifetime += layers_owned * total_tokens
            account.turns_as_worker += 1
            account.last_active_iso = _now_iso()

        spent = total_layers * total_tokens
        querier = self._upsert_account(querier_account, querier_alias)
        querier.spent_lifetime += spent
        querier.turns_as_querier += 1
        querier.last_active_iso = _now_iso()

        entry = TurnEntry(
            started_iso=_now_iso(),
            conv_id=conv_id,
            turn_idx=turn_idx,
            querier_account=querier_account,
            total_layers=total_layers,
            prefill_tokens=prefill_tokens,
            decode_tokens=decode_tokens,
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
        """Sum of balances across all accounts. Must always be zero."""
        return sum(a.balance for a in self.accounts.values())

    def as_dict(self) -> dict:
        """JSON-serializable snapshot for the /v1/credits API."""
        return {
            "version": self.VERSION,
            "accounts": {aid: asdict(a) | {"balance": a.balance}
                         for aid, a in self.accounts.items()},
            "turns": [asdict(t) for t in self.turns],
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
        # mkstemp returns an open fd we own; close it after writing.
        fd, tmp = tempfile.mkstemp(
            dir=self.path.parent, prefix=".credits.", suffix=".tmp",
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
            log.warning("could not load ledger from %s (%s); starting fresh",
                        self.path, e)
            return
        if raw.get("version") != self.VERSION:
            log.warning("ledger version mismatch (file=%s, code=%s); "
                        "starting fresh", raw.get("version"), self.VERSION)
            return
        for aid, raw_acc in raw.get("accounts", {}).items():
            # Drop computed fields that may not match across versions.
            raw_acc.pop("balance", None)
            self.accounts[aid] = Account(**raw_acc)
        for raw_turn in raw.get("turns", []):
            entry = TurnEntry(**raw_turn)
            entry.verify_zero_sum()
            self.turns.append(entry)

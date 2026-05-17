# Compute units ledger

| | |
|---|---|
| **Status** | in-progress |
| **Effort** | S |
| **Complexity** | low |
| **Risk** | low |
| **Depends on** | [[accounts-and-identity]] |
| **Unlocks** | [[cryptographic-ledger]], fairness allocation, paid tiers |
| **Branch** | `feat/compute-units-ledger` |

## Why

dots's eventual pitch is a closed credit economy: contribute compute, earn
CU; query the pool, spend CU. We have no measurement of contribution today.
Once accounts exist ([[accounts-and-identity]]), CU is the first non-trivial
thing we can attribute to them.

## What changes

- New `dots/ledger.py` — `Account`, `TurnEntry`, `Ledger` classes. Atomic
  JSON persistence to `~/.dots/credits.json`.
- `dots/hub.py:Hub` — owns a `Ledger`. After each successful turn, debits
  the querier and credits each participating worker.
- `dots/hub.py:Participant` — gains `account_id` (from HELLO).
- `dots/api.py` — `GET /v1/credits` returns the ledger; `GET /v1/credits/<acc>`
  returns one account.
- `dots/cli.py` — `dots credits` subcommand: connects to a hub, prints your
  account balance.

## The CU formula

```
earned_by_worker_i  =  layers_owned_i  ×  tokens_total
spent_by_querier    =  total_layers    ×  tokens_total
                     =  sum_i(earned_by_worker_i)
```

`tokens_total = prefill_tokens + decode_tokens`. Per-turn invariant:
**sum of all credits == debit by querier**. Always.

## Worked examples

**Solo** (one human, three Macs all under the same account, that account is
also the querier):

```
27 layers × 100 tokens = 2700 CU total.
  arpanet worker  (3 layers):    +300
  gizmos worker   (5 layers):    +500
  hfhvq3jkhy      (19 layers):  +1900
                                 ─────
  total credited to acc_arpan:  +2700

  acc_arpan as querier:         −2700
                                 ─────
  acc_arpan net change:             0  ✓
```

**Mixed** (arpan + friend; friend's gizmos contributes; arpan queries):

```
arpan workers earn:     +2200
friend worker earns:     +500
arpan as querier:       −2700
                         ─────
arpan net:   −500
friend net:  +500
pool sum:        0  ✓
```

The economy is zero-sum at the pool level. Always.

## Approach

`Ledger` maintains per-account totals and a per-turn audit log. Atomic
writes (write to `.tmp`, fsync, rename) on every flush. JSON for now;
SQLite if/when the file grows past ~10 MB.

Schema:

```json
{
  "version": 1,
  "accounts": {
    "acc_arpan": {
      "alias": "arpan",
      "earned_lifetime": 50000,
      "spent_lifetime": 48500,
      "balance": 1500,
      "turns_as_querier": 47,
      "turns_as_worker": 312,
      "first_seen_iso": "...",
      "last_active_iso": "..."
    }
  },
  "turns": [
    {
      "started_iso": "...",
      "conv_id": "...",
      "turn_idx": 5,
      "querier": "acc_arpan",
      "prefill_tokens": 80, "decode_tokens": 20,
      "total_layers": 27,
      "spent": 2700,
      "credited": {"acc_arpan": 2200, "acc_friend": 500}
    }
  ]
}
```

## Key design decisions

| Option | Decision |
|---|---|
| **Time-based CU** (`wall_clock_seconds`) | rejected — rewards slow workers; encourages stalling |
| **FLOPs-based CU** | rejected — hard to verify, complex to compute |
| **Tokens × layers** | **chosen** — simple, deterministic, matches actual work |

| Option | Decision |
|---|---|
| **Pay for output tokens only** (recovery is free for user) | rejected — violates zero-sum; mints CU from nothing |
| **Pay for compute actually performed, including re-prefill** | **chosen** — strictly conservative, transparent |

| Option | Decision |
|---|---|
| **Hub earns CU for embed + lm_head** | rejected — would require setting a rate; complicates v1 |
| **Hub is "platform overhead", not paid** | **chosen** for v1; revisit if needed |

| Option | Decision |
|---|---|
| **Negative balances rejected** | rejected — premature gating; v1 should observe before enforcing |
| **Permissive (allow negative)** | **chosen** for v1 |

## Open questions

- **Failed turns** (errored before any token output): no credits, no debit.
  Already aligned with "pay for compute performed."
- **Account aggregation across pools**: out of scope for v1. Each pool's
  ledger is local.
- **Cross-pool transfer / settlement**: out of scope. Belongs in a future
  plan on portable balance proofs.

## Test plan

- **Invariant**: for every turn recorded, `spent == sum(credited.values())`.
  Asserted in code AND in a fuzz test that generates random pool sizes and
  turn lengths.
- **Solo pool zero-sum**: end-to-end test, single-account pool, run N
  turns, assert `account.balance == 0` after every one.
- **Atomic persistence**: kill the hub mid-write, restart, assert ledger
  is either the pre-write or post-write state, never a partial.
- **Drop + recover**: assert workers credited for both passes when a
  worker drops mid-generation; querier debited for both.

## Out of scope

- Cryptographic signing of entries ([[cryptographic-ledger]]).
- Hash chain ([[cryptographic-ledger]]).
- Cross-pool portability.
- Negative-balance enforcement.
- Hub itself earning CU.
- Display in chat UI (separate cosmetic PR).

## Followups

- [[cryptographic-ledger]] — sign each turn entry, hash-chain to make
  ledger tamper-evident.
- Settlement protocols between accounts (likely never; social layer).
- Decay or seasonal reset for inactive balances.

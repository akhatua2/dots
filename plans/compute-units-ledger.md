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

> **Design rationale** — for the trade-offs we walked through (turn-CU,
> token-CU, wall-clock-CU, bytes-CU, FLOP-CU) and why we landed on
> FLOP-CU, see [[compute-units-ledger-rationale]]. Includes a section
> on why this is future-proof for GPU workers.

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

**1 CU = 1 TFLOP (10¹² floating-point operations).** Stored internally
as integer μCU (1 μCU = 10⁶ FLOPs) for exact integer math; displayed
divided by 10⁶.

```
flops_done          = 2 × active_params × tokens_total
                      (2× for forward-pass multiply-add accounting)

spent_by_querier    = flops_done                     (in FLOPs)
earned_by_worker_i  = flops_done × layers_owned_i / total_layers
                      (with rounding remainder distributed)

sum_i(earned_by_worker_i) == spent_by_querier        (zero-sum, exact)
```

`tokens_total = prefill_tokens + decode_tokens`. `active_params` is read
from the loaded model (for MoE models, this is the per-token active set
— top-k × per-expert + dense components).

For why FLOP-CU was chosen over token-CU, byte-CU, time-CU, etc., and
how this future-proofs the design for GPU workers, see
[[compute-units-ledger-rationale]].

## Worked examples

**Solo** (one human, three Macs all under the same account, querying
Qwen-7B-4bit at 7B params, 100-token turn):

```
flops_done = 2 × 7e9 × 100 = 1.4 TFLOPs

  arpanet worker  (4 of 28 layers):    +0.20 CU
  gizmos worker   (6 of 28 layers):    +0.30 CU
  hfhvq3jkhy      (18 of 28 layers):   +0.90 CU
                                       ─────
  total credited to acc_arpan:         +1.40 CU

  acc_arpan as querier:                −1.40 CU
                                       ─────
  acc_arpan net change:                 0.00 CU  ✓
```

**Mixed** (arpan + gizmo; gizmo's Mac contributes; arpan queries):

```
arpan workers earn  (22 of 28 layers): +1.10 CU
gizmo worker earns  (6 of 28 layers):  +0.30 CU
arpan as querier:                      −1.40 CU
                                       ─────
arpan net:   −0.30 CU
gizmo net:   +0.30 CU
pool sum:     0.00 CU  ✓
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

## Known limitations (v1)

These are real but accepted for the friend-pool target. Each has a
deferred follow-up plan.

| Limitation | Risk | Mitigation plan |
|---|---|---|
| **Junk-output workers earn CU for fake compute.** Worker can accept LOAD_SHARD and COMPUTE messages but return garbage hidden states. Hub doesn't verify output. | Bad actor earns CU without doing work; users get wrong responses. | spot-check-audits (deferred plan) |
| **Account_id impersonation.** HELLO is unsigned. Anyone can claim any account_id. | Inflate someone else's balance; spend their credit. | [[cryptographic-ledger]] — signed HELLO with Ed25519 challenges. |
| **Hub-operator tampering.** Hub owns the ledger file. Manual edits possible. | Operator can mint or burn CU at will. | [[cryptographic-ledger]] — hash-chained, dual-signed entries. |
| **No balance gating.** Querier can run heavy turns going deep negative; hub keeps serving. | Resource abuse (RAM, electricity); not financial in v1. | future rate-limit plan |
| **Failed-turn grief.** User triggers many failing turns; workers do prefill that's wasted. | Compute drain on contributors. | future: charge for compute done even on failure; or per-account rate limit |
| **Quantization-blindness.** A bf16 worker and a 4-bit worker earn the same CU per layer-token. The bf16 worker pays more wall-clock + electricity. | Mild unfairness against bf16 hosts; documented trade-off. | none planned — this is a feature: model has an inherent compute cost; precision is a worker efficiency choice |
| **Final-plan attribution on recovery.** If a worker dies mid-turn, the new workers get full credit; the dead worker gets none even if it did partial work. | Slight unfairness on rare failure paths. | acceptable simplification |
| **Attention FLOPs approximation.** Formula uses `2 × params × tokens`, undercounting attention's O(seq²) for long contexts. | Slight undercharging on very long contexts (32k+ tokens). Error <5% for chat-length contexts. | acceptable; refine if/when long-context becomes common |

These limitations are documented prominently so v1 users understand
what trust assumptions they're operating under. For a friend pool where
participants know each other, all are acceptable. For a public-pool
deployment, the deferred crypto + audit plans become MUSTs.

## Followups

- [[cryptographic-ledger]] — sign each turn entry, hash-chain to make
  ledger tamper-evident.
- Settlement protocols between accounts (likely never; social layer).
- Decay or seasonal reset for inactive balances.

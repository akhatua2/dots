# Cryptographic ledger

| | |
|---|---|
| **Status** | proposed |
| **Effort** | M |
| **Complexity** | medium |
| **Risk** | medium |
| **Depends on** | [[accounts-and-identity]], [[compute-units-ledger]] |
| **Unlocks** | cross-pool balance proofs, third-party audit, account portability |

## Why

The plain-JSON ledger from [[compute-units-ledger]] is trust-based: you have
to trust the hub operator not to tamper with entries. For a friend pool, that
trust is fine. For anything bigger — or for moving CU between pools — you
need entries that are *self-verifying*.

Bitcoin's design solves exactly this problem: tamper-evident, append-only,
verifiable by anyone with just the chain head. We borrow the parts that
apply (signatures + hash chain) and skip the parts that don't (proof-of-work,
consensus, mining), because the hub is still the **issuance authority** —
nobody is competing to mint CU.

We're not building a blockchain. We're building a **trusted-issuer
transparency log**, the same shape as Certificate Transparency.

## What changes

- `dots/ledger.py` — `TurnEntry` gains `prev_hash`, `entry_hash`,
  `querier_sig`, `hub_sig`. Each entry signed by querier (debit consent) and
  by hub (issuance authority).
- Worker confirmation step at the protocol level — after a turn, each
  participating worker signs an acknowledgment of its credit. Optional in v1
  (hub records solo); required if we want strict worker non-repudiation.
- New endpoint: `GET /v1/credits/proof/<account>` — returns a Merkle-style
  inclusion proof: balance + the entries that reached it + chain head hash.
- New CLI: `dots credits verify <ledger.json>` — independently checks all
  signatures and the hash chain.
- Optional: hourly anchor of the chain head to an external timestamping
  service (OpenTimestamps, free).

## Approach

### Signed entries

Every `TurnEntry` is signed by the querier (proves they consented to spend)
and by the hub (proves it was officially recorded):

```
TurnEntry {
  index:           sequential, monotonic
  prev_hash:       SHA-256 of previous entry's entry_hash
  started_iso:     timestamp
  conv_id, turn_idx
  querier:         acc_arpan public key
  total_layers, total_tokens, spent
  credited:        {acc_X: cu, acc_Y: cu, ...}
  querier_sig:     Ed25519 sig over (index || prev_hash || conv_id || ...) by querier
  hub_sig:         Ed25519 sig by hub (proves official recording)
  entry_hash:      SHA-256 over the whole entry (including both sigs)
}
```

The `prev_hash` makes the ledger a hash chain. Any tampering with an
old entry — changing a value, deleting an entry, inserting one in the
middle — breaks every `entry_hash` downstream, immediately detectable
when verifying.

### Why both signatures

- `querier_sig` proves the querier authorized the spend. Hub can't forge
  debits against accounts whose keys it doesn't control.
- `hub_sig` proves the hub officially recorded it. Workers / queriers
  can't forge credits to themselves — only the hub's signature gives an
  entry weight.

Bitcoin uses signatures for the same reason (UTXO ownership proof) plus
proof-of-work for ordering. We don't need ordering consensus — the hub is
the ordering oracle. We just need ordering *integrity*.

### Worker acknowledgments (optional v2)

Strictest version: each participating worker also signs an
acknowledgment of its credit ("I, acc_friend, agree I was credited 500
CU for turn N"). Prevents the hub from over-crediting a worker to make
the books look better than they are. Adds one round of signatures per
turn — slower. Skip for v1; add when the threat model warrants it.

### Anchoring

Once an hour (or every 1000 entries), the hub publishes the current
chain head somewhere immutable:

- **OpenTimestamps** (free, anchors to the Bitcoin blockchain) — best
- **A signed tweet from a public account** — cheap, weaker
- **A Git commit to a public repo** — easy, weaker

After anchoring, the hub operator can't rewrite history past the anchor
point even if they wanted to — the anchor is a public commitment.

### Verification

Anyone can verify a ledger end-to-end:

```bash
dots credits verify ~/.dots/credits.json
# walks the chain:
#   1. for each entry, recompute entry_hash → matches?
#   2. for each entry, verify querier_sig with the public key?
#   3. for each entry, verify hub_sig against the hub's published public key?
#   4. for each entry, prev_hash matches previous entry's entry_hash?
#   5. for each anchor in the file, verify the anchored hash is reachable?
```

No hub running. No trust needed. Just the file + the chain of public keys.

## Key design decisions

| Option | Decision |
|---|---|
| **Full blockchain (PoW or PoS consensus)** | rejected — overkill, expensive, no decentralization need |
| **Plain Merkle tree (CT-style)** | considered — fine but log-structured chain is simpler for this size |
| **Hash-chained signed log** | **chosen** — minimum machinery to get tamper-evidence |

| Option | Decision |
|---|---|
| **One signature: hub only** | rejected — hub could mint CU at will |
| **Querier sig + hub sig** | **chosen for v1** |
| **Querier + hub + every credited worker** | considered — strictest, defer to v2 |

| Option | Decision |
|---|---|
| **Anchor to Bitcoin via OpenTimestamps** | **chosen** for v2 — free, gold-standard |
| **Anchor to Ethereum** | rejected — costs gas |
| **Anchor to a Git repo** | acceptable fallback if OTS is unavailable |

## Open questions

- **Hub key rotation.** If the hub operator regenerates its key, all
  prior entries are still verifiable (the old public key is in the
  entries), but future entries need a new public key. Handle by including
  a "hub keypair change" event in the chain, signed by both old and new.
- **Multiple hubs / pool splits.** Each pool's chain is independent. A
  user moving between pools carries a signed balance proof but starts a
  new history on each pool.
- **GDPR / right-to-be-forgotten.** Immutable logs and "delete my data"
  are tensions. Mitigation: store only account public keys + amounts in
  the log, not personal data. Aliases stored separately, can be deleted.
- **Performance.** Ed25519 verification is ~50 µs; one extra sig
  per turn is negligible. SHA-256 hashing is in the same range. Anchoring
  is once per hour, doesn't touch hot path.

## Test plan

- **Tamper detection**: write the ledger, change one value in one
  entry, run `dots credits verify`, assert it reports the breakpoint.
- **Forgery resistance**: try to forge an entry by another account
  (using a wrong key). Verification rejects.
- **Signature roundtrip**: every signed entry verifies with the
  declared public key.
- **Chain continuity under crashes**: kill the hub mid-write of a new
  entry. Restart. Ledger either contains the new entry fully (signed,
  hashed) or doesn't contain it at all.
- **Anchor verification**: publish anchor to OpenTimestamps in a test
  environment; later retrieve and verify the anchor still maps to the
  same chain head.

## Out of scope

- Real decentralization (no consensus among hubs).
- Smart-contract-style spending conditions (CU is unconstrained
  fungible internal currency, not programmable).
- ZK proofs of balance (interesting future research; not v1).
- Negative balance enforcement (separate plan).

## Followups

- Spot-check audits ([[spot-check-audits]]) can sign-on the ledger as
  a "validator" role — third-party watcher verifies entries match
  expected work.
- Cross-pool transfer protocol: a signed balance proof from pool A is
  presented to pool B; pool B credits the equivalent based on a
  bilateral exchange rate. Far-future.
- A web viewer that mirrors the ledger and lets anyone audit ("dots
  block explorer").

# Accounts and identity

| | |
|---|---|
| **Status** | proposed |
| **Effort** | S |
| **Complexity** | medium |
| **Risk** | low |
| **Depends on** | none |
| **Unlocks** | [[compute-units-ledger]], [[cryptographic-ledger]], spot-check audits |

## Why

Today every dots process has a per-machine UUID (`~/.dots/identity`) used
purely for connection-level bookkeeping (reconnect-takeover). There's no
notion of a **human** — the person who *owns* a contribution or who *issued*
a query. Without that, we can't build a credit economy, can't gate access,
can't run audits.

One person may run several workers across several Macs. We need contributions
from all of them to aggregate under one account, and we need to be able to
*prove* a worker (or a chat client) belongs to that account.

## What changes

- New file: `~/.dots/account.key` — Ed25519 private key for the account.
- New `dots/account.py` module: keypair generation, signing, verification.
- New CLI subcommand: `dots account {create, export, import, show, set-alias}`.
- HELLO message in `dots/worker.py` and chat connection in `dots/chat.py` gain
  three fields: `account_id` (public key), `nonce`, `signature`.
- Hub validates signatures in `dots/hub.py:_on_connect` before admitting.
- New `dots/util.py` helpers: `load_or_create_account()`, `account_id_short(pk)`.

## Approach

Identity is layered:

| Layer | Persistence | Purpose |
|---|---|---|
| `worker_id` (existing) | per-process UUID, ephemeral | TCP connection routing, reconnect-takeover |
| device identity (existing `~/.dots/identity`) | per-Mac UUID | logging, "which physical box is this" |
| **account** (new) | Ed25519 keypair in `~/.dots/account.key` | who **earns/spends CU**, owns participation |

The same `account.key` copied across N devices makes all N workers credit
the same account. That's how "one user, many workers" works:

```
account: acc_abc123 (Ed25519 public key fingerprint)
  ├── Mac 1: dots/identity_1, runs worker process #1
  ├── Mac 2: dots/identity_2, runs worker process #2
  └── Mac 3: dots/identity_3, runs worker process #3
            (and chat client #4)
```

HELLO carries a signature of `(worker_id || account_id || hub_nonce)` using
the account's private key. The hub verifies it against the public key before
admitting the worker. Without the key, you cannot impersonate an account.

Initial UX:

```bash
# First time on Mac 1:
$ dots account create
generated account: acc_abc123def4...
private key stored in ~/.dots/account.key (do not share)

# To replicate to Mac 2:
$ dots account export > account.toml      # on Mac 1
# transfer account.toml securely (USB, scp, signal, etc.)
$ dots account import < account.toml      # on Mac 2

# To set a human-readable label (purely cosmetic, shown in logs):
$ dots account set-alias arpan
```

## Key design decisions

| Option | Decision |
|---|---|
| **Trust file: account_id is just a UUID in a file** | rejected — anyone who learns your account_id can mint workers under your name |
| **Hub-issued tokens** | rejected — requires hub-side user DB; not portable across pools |
| **Ed25519 keypair, public key is account_id** | **chosen** — self-authenticating, portable across hubs, sets foundation for [[cryptographic-ledger]] |

Why Ed25519 specifically: small keys (32 bytes), fast verification, single
established Python library (`cryptography` or `pynacl`). Bitcoin-style
secp256k1 also works but Ed25519 is simpler.

Why account_id is the public key, not a separate UUID: self-authenticating.
You can verify a signed entry without consulting any registry — the public
key is in the entry itself.

## Open questions

- **Multi-key per account?** Useful if a user has a "master" key and wants
  per-device subkeys (so losing one Mac doesn't compromise the master). Adds
  complexity. Punt to v2.
- **Account alias collisions?** Aliases are purely cosmetic, so collisions
  don't matter for correctness. The hub may warn if "arpan" already exists
  under a different public key. Not a hard error.
- **Account recovery?** If you lose `~/.dots/account.key`, your CU history
  is gone — there's no way to prove you owned the old account without the
  key. Same as losing a Bitcoin wallet. Mitigation: encourage exporting
  `account.key` to a password manager / secure backup.
- **Should the chat client have a separate per-session signing key?**
  Currently chat reuses the device-level worker_id UUID (per-session fresh).
  Account signing can stay device-level. The chat process uses the account
  key for HELLO but doesn't expose it on the wire beyond that.

## Test plan

- Round-trip key serialization (create → export → import → identity matches).
- HELLO signature verification: valid passes, tampered fails, expired nonce
  rejected.
- Replay protection: same signed HELLO rejected on second use (nonce
  freshness).
- Cross-device test: spawn two workers with the same `account.key`, both
  admitted under the same account_id.
- Cross-device with *different* keys: each admitted as its own account.

## Out of scope

- The actual credit ledger ([[compute-units-ledger]]).
- Hash chaining / Merkle proofs ([[cryptographic-ledger]]).
- Hub-issued capability tokens (future).
- Recovering a lost key.

## Followups

- [[compute-units-ledger]] uses `account_id` as the ledger key.
- [[cryptographic-ledger]] uses the account's private key to sign ledger
  entries.
- A future hardening: per-device subkeys with delegation from the master
  account key.

# Accounts and identity

| | |
|---|---|
| **Status** | in-progress |
| **Effort** | S |
| **Complexity** | low |
| **Risk** | low |
| **Depends on** | none |
| **Unlocks** | [[compute-units-ledger]] |
| **Branch** | `feat/accounts-and-identity` |

## Why

Today every dots process has a per-machine UUID (`~/.dots/identity`) used
purely for connection-level bookkeeping (reconnect-takeover). There's no
notion of a **human** — the person who *owns* a contribution or who *issued*
a query. Without that, we can't build a credit economy, can't gate access,
can't run audits.

One person may run several workers across several Macs. We need contributions
from all of them to aggregate under one account.

## What changes

- New file: `~/.dots/account` — UUID4 string identifying the account.
- New `dots/account.py` module: dataclass + load/create helpers.
- New CLI subcommand: `dots account {show, set-alias}`.
- HELLO message in `dots/worker.py` gains an `account_id` field.
- `dots/hub.py:Participant` gains an `account_id` field; logged on join.
- `dots/util.py` helpers: `load_or_create_account()`, `account_id_short()`,
  `load_account_alias()`, `save_account_alias()`.

## Approach

Identity is layered:

| Layer | Persistence | Purpose |
|---|---|---|
| `worker_id` (existing) | per-process UUID, ephemeral | TCP connection routing, reconnect-takeover |
| device identity (existing `~/.dots/identity`) | per-Mac UUID | persistent worker identity |
| **account** (new) | UUID in `~/.dots/account` | who **earns/spends CU**, owns participation |

The same `~/.dots/account` file copied across N devices makes all N workers
credit the same account. That's how "one user, many workers" works:

```
account: acc-abc123def456...
  ├── Mac 1: identity_X, runs worker process #1
  ├── Mac 2: identity_Y, runs worker process #2
  └── Mac 3: identity_Z, runs worker process #3
            (and chat client #4)
```

HELLO payload gains `account_id`. The hub records it on the `Participant`
and uses it for future ledger entries. **No verification in v1** — anyone
can claim any account_id. For a friend pool this is fine; it's a pre-trust
identity, not an authenticated one.

Aliases (human-readable labels) are purely cosmetic, stored separately in
`~/.dots/account-alias`. Used in log lines like `worker[abc12345 · arpan]`.

Initial UX:

```bash
# Show this device's account_id (creates one on first call)
$ dots account show
acc-abc123def4...
alias: (unset)

# Set a human-readable alias (purely cosmetic)
$ dots account set-alias arpan

# Replicate to another Mac (manual copy):
$ cat ~/.dots/account              # on Mac 1, print
$ vim ~/.dots/account              # on Mac 2, paste the same UUID
```

## Key design decisions

| Option | Decision |
|---|---|
| **Ed25519 keypair, signed HELLO** | rejected for v1 — overkill; deferred to [[cryptographic-ledger]] |
| **Hub-issued tokens** | rejected — adds state to the hub |
| **Plain UUID in a file** | **chosen** — minimum viable, ships fast, social trust is fine for friend pools |

| Option | Decision |
|---|---|
| **Single file with both identity + account** | rejected — different scopes; per-device vs per-user |
| **Two separate files: `identity` and `account`** | **chosen** |

| Option | Decision |
|---|---|
| **Auto-load on Worker construction (with optional override)** | **chosen** — mirrors `load_or_create_identity()` |
| **Pass account in via env var** | rejected — surprising, not Mac-native |

## Open questions

- **Account alias collisions?** Aliases are cosmetic, no enforcement.
  Multiple accounts can share the same alias; only the account_id is
  authoritative.
- **Account recovery?** Lose `~/.dots/account` and your CU history is
  gone. Workaround: keep a backup of `~/.dots/account` (it's just a UUID).
- **Sharing across devices?** Manual file copy for v1. We could add
  `dots account export/import` later, but it'd just be `cat`/`tee` since
  there's no key material.
- **Hub side authoritative aliases?** v1: hub displays whatever alias the
  client advertises (none for now). Later, hub could maintain its own
  alias registry.

## Test plan

- `load_or_create_account()` round-trip: writes UUID, re-reads, matches.
  Idempotent across calls.
- CLI: `dots account show` after fresh setup creates the file.
- CLI: `dots account set-alias foo` persists the alias.
- HELLO carries `account_id`; hub stores it on `Participant`.
- Hub log line includes account_id (short) when a worker joins.
- Existing tests still pass with the new HELLO field.

## Out of scope

- Crypto signatures on HELLO ([[cryptographic-ledger]]).
- Account export/import (manual copy is fine for v1).
- Verification that a worker actually controls the claimed account.
- Hub-side alias registry / collision handling.
- The actual CU ledger ([[compute-units-ledger]]).

## Followups

- [[compute-units-ledger]] uses `account_id` as the ledger key.
- [[cryptographic-ledger]] upgrades the account file to a keypair and
  adds signed HELLO.

# plans/

A folder of small design documents for features we want to ship eventually.
Each plan is a single markdown file. You can pick one up at any time —
they're written to be self-contained.

## Why this exists

dots is a research-grade system with a lot of architectural decisions ahead
of it. We've discussed many of them but they live in chat logs and memory.
This folder is the single source of truth for **what we plan to build, why,
and what it depends on**, so future-us (or anyone joining) can pick up where
we left off.

These are not promises. A plan being here means we've thought about it. It
may never ship. The status field reflects current intent.

## How to write a plan

1. Copy `TEMPLATE.md` to a new file in this folder with a kebab-case name
   (e.g. `partial-weight-loading.md`).
2. Fill in the fields. Be terse — the goal is a designer of average context
   reading this in 3 minutes and knowing what to do.
3. Link to related plans in the body as `[[other-plan-name]]`.
4. Update `index` below.

## Status workflow

```
proposed  → accepted  → in-progress  → done
                                  └→ abandoned
```

- **proposed** — written down, not committed to. Anyone can propose.
- **accepted** — we agree this is worth shipping. May not be scheduled yet.
- **in-progress** — someone is actively building it. Branch name in the
  plan.
- **done** — merged to main. Plan stays as historical record.
- **abandoned** — decided not to do. Plan stays with reasoning.

## Sizing conventions

| Effort | Wall-clock |
|---|---|
| **XS** | < half day |
| **S** | 1–2 days |
| **M** | 1 week |
| **L** | 2–4 weeks |
| **XL** | > 1 month |

| Complexity | Meaning |
|---|---|
| **low** | Established pattern; one obvious correct approach. |
| **medium** | Some unknowns; design exploration likely. |
| **high** | Research-grade; multiple unknowns; may pivot. |

| Risk | Meaning |
|---|---|
| **low** | Contained to a few files; trivially revertable. |
| **medium** | Touches multiple subsystems; mid-effort to roll back. |
| **high** | Protocol or storage changes; rollback requires migration. |

## Index

Plans are listed in roughly dependency order. Earlier ones unlock later ones.

### Foundations

- [`accounts-and-identity.md`](./accounts-and-identity.md) — Ed25519
  account identity, persistent across devices. Prerequisite for the
  credit economy.
- [`compute-units-ledger.md`](./compute-units-ledger.md) — Per-turn
  CU earn + spend tracking with zero-sum conservation.
- [`cryptographic-ledger.md`](./cryptographic-ledger.md) — Hash-chained,
  signed ledger entries; tamper-evident; Bitcoin-inspired hardening of
  the basic ledger.

### Cloud + ops

- [`mac-launchagent-hub.md`](./mac-launchagent-hub.md) — Always-on hub
  on a Mac via LaunchAgent + auto-deploy on git push.
- [`mlx-decoupled-hub.md`](./mlx-decoupled-hub.md) — Refactor hub to not
  require MLX, so it can run on Linux.
- [`cloud-hub-deploy.md`](./cloud-hub-deploy.md) — Deploy hub to
  Oracle Cloud Free tier ($0/month) once MLX dependency is gone.

### Model-loading + RAM

- [`partial-weight-loading.md`](./partial-weight-loading.md) — Workers
  load only their assigned layer slice from disk. Unlocks 70B+ models on
  consumer Macs.

### Performance + topology

- [`microbatching.md`](./microbatching.md) — N tokens in flight through
  the pipeline at once. Amortizes per-stage network cost.

### Discussed but not yet written down

These came up in design conversations but don't have plan docs yet. Write
them when you're ready to schedule:

- **Hybrid pipeline** — hub stays as control + planning; workers ship
  hidden states directly to each other for the data plane; workers
  optimistically mirror their output back to the hub asynchronously so
  the hub keeps full observability without being on the critical path.
  Major architectural shift; deep dependency for usable big-pool latency.
- **Multi-backend pool** — protocol-first design where each worker
  declares its inference stack (MLX, llama.cpp, vLLM, …). Hub stays
  framework-agnostic. Hidden-state-on-the-wire is the contract.
- **Spot-check audits** — hub re-runs ~1% of worker computations on a
  different worker (or itself) to detect cheating. Almost free once the
  hub already has shadow state from the hybrid-pipeline work.

## Picking what to ship next

If you don't know where to start, look for plans where:

- Status is **accepted** (not yet started)
- All `Depends on` items are **done**
- Effort matches the time you have

Foundations come first because everything else builds on them.
[`compute-units-ledger.md`](./compute-units-ledger.md) and
[`partial-weight-loading.md`](./partial-weight-loading.md) are both
small, both independent, and both unlock material future work.

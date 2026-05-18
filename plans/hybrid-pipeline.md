# Hybrid pipeline (coordinator-only hub)

| | |
|---|---|
| **Status** | proposed |
| **Effort** | L |
| **Complexity** | high |
| **Risk** | high |
| **Depends on** | none directly; lands alongside [[resource-scheduling]] and [[worker-security-model]] |
| **Unlocks** | [[cloud-hub-deploy]] at any scale; public dots pool; multi-model serving on one hub; supersedes [[mlx-decoupled-hub]] |
| **Branch** | (set when in-progress) |
| **PR** | (set when in-progress) |

## Why

The hub today is on the per-token hot path. For every token generated it runs
input embedding lookup, the final `norm + lm_head` matmul (~600 M weights for
Qwen-7B), and sampling. The pipeline routes every hidden state through the hub
between workers.

For a private hub serving one model to a few friends this is fine. Three
roadmap scenarios make it fatal:

1. **Public dots pool** — many concurrent users querying through one hub; hub
   becomes the matmul bottleneck.
2. **Multi-model serving on one hub** — each model needs its own embed +
   lm_head resident on the hub. RAM scales linearly with model count.
3. **Hub on a cheap VPS / Pi / phone** — the box cannot do the matmul at all.

If the hub does **zero per-token compute**, it runs on anything. Cost goes from
"needs a real machine" to "can be a side process on whatever you have." That's
the unlock.

## What changes

The architectural shift is twofold and inseparable:

1. **Workers are resource bags, not roles.** A worker advertises what it has
   (RAM, CPU, GPU, disk, supported backends) and the hub places **work units**
   onto it. There is no "head worker" role, no "layer worker" role — there are
   only resources and the units that fit on them.
2. **The hub is off the per-token data plane.** Workers send hidden states
   directly to each other. The hub plans, observes (optionally), and writes
   the ledger; it does not relay packets.

The "where to place which unit" problem is owned by [[resource-scheduling]].
The "how do workers talk safely" problem is owned by [[worker-security-model]].
This plan owns the data-plane change itself.

### Code surface

- **`dots/hub.py`** — `_head`, `_sample`, and every `embed_tokens` call goes
  away. Hub loads no model weights. Hub stops importing MLX entirely.
  `_pipeline` is replaced with a single `start_generation` dispatch to the
  worker hosting the embed unit.
- **`dots/worker.py`** — gains the ability to host arbitrary work units
  (not just decoder-layer shards). Runs a local execution loop when assigned
  the embed → first-layer → ... → lm_head → sample chain entry point.
- **`dots/protocol.py`** — new messages:
  - `start_generation` (hub → worker_with_embed): prompt_ids, max_tokens,
    temperature, chain_addresses (where each downstream unit lives)
  - `token` (entry-point worker → hub, streamed): generated token + index
  - `generation_done` (entry-point worker → hub): prefill_tokens,
    decode_tokens, for the ledger
  - `compute_unit` (peer ↔ peer): hidden state in, hidden state out
- **`dots/shard.py`** — generalized: a worker can hold multiple work units
  (e.g. embed + layers 0–7 + lm_head + sampler all on one machine if RAM
  allows; or just layers 8–19 on a smaller machine).

### Observability mirror (optional)

Each worker may asynchronously ship a shadow copy of its output to the hub.
The hub stores per-turn audit data without being on the critical path. This
is what unlocks spot-check audits and [[cryptographic-ledger]] later.

## Approach

### Hub after this lands

```
┌─── Hub (any host, no MLX, no model weights) ───┐
│                                                 │
│  • peer discovery + pool management             │
│  • per-query: ask scheduler for a placement,    │
│    broadcast the plan                           │
│  • streaming relay: entry-point worker → chat   │
│  • ledger: record turn at end                   │
│                                                 │
│  not on per-token hot path                      │
└─────────────────────────────────────────────────┘
```

### Per-turn data flow

```
  chat client
       │ prompt
       ▼
     hub  ── plan { unit_assignments, chain_order } ──► workers
       │
       ▼ start_generation(prompt_ids, max_tokens, temp)
     worker hosting embed unit (call it W_entry)
       │
       │  for each token:
       │     hidden = embed(token_id)
       │     hidden = next_in_chain.compute_unit(hidden) ─► ... ─►
       │              ... ─► last_in_chain.compute_unit(hidden) ─► W_entry
       │     token  = sample(norm(hidden) @ lm_head)
       │     yield  token ─► hub ─► chat client
       │
       │  at end: generation_done(prefill_tokens, decode_tokens)
       ▼
     hub: record ledger turn, attribute CU
```

Hub sees plan → token stream → done. Not a single matmul.

### Multi-model on one hub

Each model has its own (work-unit graph, placement). The hub maintains plans
keyed by `model_id`. A worker can be assigned work units from multiple models
simultaneously, capacity permitting. The scheduler ([[resource-scheduling]])
decides who hosts what.

### Failure handling

A worker drops mid-generation. The hub detects (no token in N seconds,
peer disconnect), notifies the chat client, optionally asks the scheduler
for a replan and retries. Existing layer-worker drop logic extends to the
unit-based model.

## Key design decisions

| Option | Decision |
|---|---|
| **Star topology, hub on data path** (today) | rejected — defeats the goal |
| **Worker-to-worker direct, hub off path** | **chosen** |
| **Full mesh, any worker can be entry point** | rejected — too much per-turn state |

| Option | Decision |
|---|---|
| **Entry-point worker streams tokens to chat client directly** | rejected — chat client may not be reachable from worker (NAT); hub should stay as front door |
| **Entry-point worker streams tokens to hub, hub forwards** | **chosen** — small bandwidth, stable client connection point |
| **Batch tokens before sending** | considered — defer; per-token cost is already small |

| Option | Decision |
|---|---|
| **Hub keeps a fallback copy of embed/norm/lm_head** | rejected — defeats the purpose; fail clearly over degrade silently |
| **Hub never loads model code** | **chosen** — clean cut, no MLX import in hub |

| Option | Decision |
|---|---|
| **Named roles ("head worker", "layer worker")** | rejected — embeds policy in protocol |
| **Workers as resource bags, scheduler places units** | **chosen** — generic enough for future work types (MoE experts, KV offload, etc.) |

| Option | Decision |
|---|---|
| **Workers connect directly for the chain** | **chosen** for v1 (same LAN / tailnet) |
| **All worker-to-worker traffic relayed through hub** | rejected — puts hub back on data path |
| **NAT traversal for cross-network direct connections** | deferred — overlay (Tailscale / WG) handles it |

## Open questions

- **KV cache lifetime on the entry-point worker.** Long conversations need
  the embedding-side cache to survive across turns. Either pin the entry
  worker per-conv, or migrate KV on replan. Lean: pin per-conv.
- **Token streaming buffer behavior.** If the chat client disconnects
  mid-stream, do we abort? Likely yes — saves compute on entry worker.
- **Tokenizer location.** Today hub does it. Either move to entry-point
  worker (consistent with "hub does no model code") or keep on hub
  (tokenizers are tiny and CPU-only). Lean: move it — purity over saving
  a few KB.
- **Streaming backpressure.** If chat client is slow, do we backpressure
  the entry-point worker, or buffer at the hub? Lean: bounded buffer at
  hub with backpressure as fallback.
- **Backwards compatibility during migration.** Worth a flag for one
  release? Lean: no — protocol break contained, no external users.

## Test plan

- **Invariant: hub does no model compute.** Static check on
  `dots/hub.py` — no `lm_head`, `embed_tokens`, `sample`, or `mx.array`
  references on the per-turn code path.
- **Invariant: hub does not import MLX.** `import dots.hub` on a Linux
  box with no `mlx` package installed must succeed.
- **Token equivalence.** Same prompt + seed → coordinator-only hub
  produces identical tokens to today's hub. Real small model, not fakes.
- **Cloud hub smoke.** Hub on Linux VM (no MLX), workers on Mac, real
  chat turn works.
- **Multi-model.** Same pool, two models, two concurrent conversations
  from two clients. Both complete. Ledger correct for both.
- **Failure: entry-point worker drops mid-generation.** Hub notifies
  client; no ledger entry. New request gets a fresh placement.
- **Memory: hub RSS unchanged with multiple models.** Adding a model
  to the served set should not meaningfully grow hub resident size.
- **Ledger zero-sum holds.** Existing fuzz test passes unchanged.

## Out of scope

- **Resource-aware placement / latency-aware chain ordering.** Owned by
  [[resource-scheduling]]. Hybrid-pipeline ships with a trivial scheduler
  (e.g. place everything on one worker if it fits, else round-robin).
- **Peer authentication, scope-split protocol, sandboxing.** Owned by
  [[worker-security-model]].
- **Cross-network direct worker-to-worker connections.** v1 assumes same
  LAN or same overlay. Handled by overlay network (Tailscale / WG).
- **Multiple concurrent entry-point workers for one model.** Single
  entry-point per turn in v1; horizontal scale is a separate concern.
- **Direct entry-worker → chat-client streaming.** Hub stays as front door.

## Followups

- [[cloud-hub-deploy]] — becomes the cheap default; hub truly is small.
- [[mlx-decoupled-hub]] — **superseded.** Once hub has no model code,
  there's no MLX call site to decouple. Mark abandoned on merge.
- [[resource-scheduling]] — replaces the trivial scheduler with the
  latency-aware constrained-shortest-path placement.
- [[worker-security-model]] — formalizes the peer-to-peer auth and
  scope split this plan opens up.
- [[multi-backend-pool]] — once units are placed on workers (not on a
  framework-specific hub), workers can run units on whatever backend
  they support.
- [[cryptographic-ledger]] — the entry-point worker becomes a natural
  co-signer of turn entries; the observability mirror provides the
  audit feed.

# Microbatching

| | |
|---|---|
| **Status** | proposed |
| **Effort** | M |
| **Complexity** | medium |
| **Risk** | medium |
| **Depends on** | none (works on current topology); strongest with [[ring-topology]] |
| **Unlocks** | usable solo-chat tok/s on big-pool deployments; meaningful multi-user serving |

## Why

Today's pipeline is `batch=1`: one token traverses workers one at a time.
Per-token wall-clock is `sum(per_worker_compute) + sum(per_worker_RTT)`.
Workers are idle 3/4 of the time on a 4-worker pool.

With microbatching, multiple tokens are in flight through the pipeline at
once. Steady-state throughput becomes `max(per_stage_compute, per_stage_RTT)`
instead of the sum. For a 64-worker pool over Tailscale, that's the
difference between 0.9 tok/s and 50 tok/s.

There's a catch: for single-user autoregressive decode, you can't just put
multiple sequential tokens in flight because each depends on the previous.
This plan covers all three places microbatching *does* help and how to
implement them.

## What changes

- `dots/hub.py:Hub._pipeline` — refactored to drive multiple in-flight units
  simultaneously rather than awaiting each worker serially.
- `dots/peer.py` — already supports multiple in-flight `call()`s
  (each carries `rid`); confirm this scales to dozens of concurrent
  requests per connection.
- `dots/worker.py:Worker._handle_compute` — must handle multiple compute
  requests in flight; ensure `_shard_lock` doesn't serialize between
  different conversations.
- `dots/shard.py:Shard.run_layers` — already takes `conv_id`; verify it
  correctly tracks per-conversation KV cache when multiple are interleaved.

## The three places microbatching helps

### 1. Prefill

A 1000-token prompt is 1000 independent positions to compute. They can all
flow through the pipeline overlapping each other. **Free speedup for prefill,
no other dependencies.**

This is the easiest place to ship microbatching first.

### 2. Multi-user serving

If two users are chatting simultaneously, their token streams are
independent. Hub interleaves them into the pipeline. With B concurrent
users, effective throughput per user is unchanged but pool utilization
goes way up.

For OpenAI-API users hitting `/v1/chat/completions` concurrently, this is
where dots becomes a real serving platform.

### 3. Speculative decoding (solo-user single-conversation)

Run a small "draft" model locally on the hub (e.g. Qwen-0.5B). It proposes
the next K tokens speculatively. The full pipeline verifies all K positions
in parallel as a single batched forward pass. If the first M of K positions
match the draft, the user advances M tokens for the cost of one circuit.

For typical text, M is usually 3-6 out of K=8. That's a 3-6x speedup on
single-user decode. The math:

```
without spec: 1 token per circuit          = 1 / circuit_time
with spec   : M tokens per circuit          = M / circuit_time
              where M ≈ 3-6 in practice
```

Composes cleanly with microbatching: the K verification tokens are
just a batch of size K.

## Approach

Phase one (prefill only):
- Hub sends `compute(seq_len=N)` to first worker with the full prompt.
- Worker computes its layer slice over all N positions, returns
  `[1, N, hidden]`.
- Hub forwards to next worker, etc.
- For per-stage *batched compute*, ensure each worker uses MLX's
  batched matmul over the sequence axis (mlx-lm does this already).

Phase two (multi-user):
- Hub maintains a *queue* of pending units. Each unit = (conv_id,
  token_index, hidden_state_at_layer_K).
- Pipeline driver picks up units from the queue and sends to the
  appropriate next worker, max N in flight per worker connection.
- Worker compute is per-conv (KV cache is per-conv), so multiple
  units from different convs interleave on the same worker.

Phase three (speculative decoding for solo-user):
- Hub loads a small draft model (e.g. mlx-community Qwen2.5-0.5B-4bit).
- After each accepted token, draft model emits K speculative tokens.
- Hub bundles the K tokens as one batch, runs them through the
  pipeline (each token a unit, K units in flight together).
- For each verified position, compare full-pipeline logits to draft;
  accept the longest matching prefix.

## Key design decisions

| Option | Decision |
|---|---|
| **One concurrent unit per worker per conv** | considered — limits to per-conv concurrency only |
| **Multiple concurrent units per worker, mixed across convs** | **chosen** — enables multi-user batching |

| Option | Decision |
|---|---|
| **Static max-in-flight = N** | **chosen for v1** (`--max-in-flight 8`) — predictable memory |
| **Dynamic backpressure based on worker latency** | future optimization |

| Option | Decision |
|---|---|
| **Ship prefill first, defer multi-user and speculative** | **chosen** — biggest immediate win, smallest blast radius |
| **Ship all three together** | rejected — too big for one PR |

## Open questions

- **KV cache thrashing.** With multiple convs in flight, each worker may
  hold N KV caches simultaneously. Memory budget?
- **Token ordering within a conv.** Output token N must commit before
  input token N+1's pipeline starts. Already true in current design,
  but verify with microbatching.
- **Network framing overhead.** Many small in-flight RPCs vs few big
  ones. msgpack overhead might matter at high batch sizes — measure.
- **Speculative model dispatch.** Hub picks a draft model that
  matches the served model's tokenizer + vocab. Document the constraint.

## Test plan

- **Prefill throughput**: with a 2000-token prompt, measure wall-clock
  prefill on 3 workers, baseline vs microbatched. Expect 3-5x speedup.
- **Multi-user throughput**: simulate 4 concurrent chat clients,
  measure per-user tok/s. Expect roughly the same per-user rate as
  solo, scaling pool utilization with N users.
- **Speculative decoding**: with Qwen-0.5B as draft and Qwen-7B as
  target, measure tok/s on solo chat. Expect 2-4x speedup over plain
  pipeline depending on prompt.
- **Correctness invariants**: every microbatched run produces the
  same tokens as the unbatched baseline for greedy sampling.

## Out of scope

- Cross-pool batching.
- Continuous batching à la vLLM (different abstraction; complex; not
  needed at our scale).
- Different draft models per conversation.

## Followups

- [[ring-topology]] amplifies microbatching dramatically: with
  workers feeding directly into each other, in-flight units overlap on
  both network *and* compute, vs interleaving only at the hub.
- Auto-detect when speculative decoding is worth enabling (long-
  context tasks benefit more).

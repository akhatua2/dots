# How we picked the CU formula (design rationale)

This is the design journal for [[compute-units-ledger]]. It records the
trade-offs we walked through and why we landed on FLOP-CU. Read this
when you're wondering "wait, why didn't we use tokens?" or proposing a
new economy.

## The central question

> What does **1 CU** actually represent?

This is the load-bearing decision. Once you pick the answer, every other
question (numbers being small, fair across models, cross-pool portable,
gameable or not) follows from it.

We considered six candidates and rejected five.

---

## The principles a "most correct" ledger should satisfy

Ten properties, in priority order. Items 1–4 and 7–8 are MUSTs.
Item 9 is a UX want. Items 5, 6, 10 are nice-to-have today but become
MUSTs as soon as multi-model pools ship.

| # | Principle | What it means |
|---|---|---|
| 1 | **Conservation (zero-sum)** | Every CU credited == a CU debited. Pool sum is 0 always. |
| 2 | **Proportional attribution** | Workers earn proportional to their share of work. Querier pays for what was produced. |
| 3 | **Hardware-neutral** | A query on a fast Mac vs slow Mac shouldn't cost different CU. |
| 4 | **Stable & deterministic** | Same inputs → same numbers. No clock-skew, no hardware-variance. |
| 5 | **Model-aware** | A token of Qwen-32B work is much more compute than a token of Qwen-0.5B. |
| 6 | **Quantization-aware** | Sometimes desired (memory bandwidth differs), sometimes not (FLOPs are identical). |
| 7 | **Verifiable** | A third party with only public state can reconstruct totals. |
| 8 | **Hard to game** | Workers can't claim CU for work they didn't do. |
| 9 | **Bounded magnitude** | Numbers stay readable for years of realistic use. |
| 10 | **Cross-pool portable** | Balances on one hub should mean the same on another hub running the same model. |

---

## The candidates

### Layer-token CU (1 CU = 1 layer × 1 token of compute)

**Formula:** `spent = total_layers × total_tokens`

What we started with. Simple. Universal.

| Property | Score |
|---|---|
| Numbers | **fails** — huge. After 8 short chats: ~12,000 CU. After a year of use: tens of millions. |
| Cross-model | partial — bigger model has more layers, so cost scales somewhat |
| Cross-quantization | ✗ — quantization doesn't change layer count |

User pushback: "I would like MUCHHHH SMALLER NUMBERS." Killed.

### Token CU (1 CU = N tokens)

**Formula:** `spent = total_tokens / N`

Most intuitive. Small numbers. "I sent a 60-token chat, I owe 60 CU."

| Property | Score |
|---|---|
| Numbers | ✓ tunable via N |
| Intuitive | ✓ matches API economics |
| Model-aware | **✗ — fails badly.** A Qwen-32B token costs ~64× more compute than a Qwen-0.5B token but the same CU. Workers hosting big-model layers are systematically underpaid. |
| Cross-pool | ✗ unless N is the same constant everywhere |

For a strictly single-model pool, this is fine. The moment you support
multiple models, it breaks. We rejected it once the architecture was
clarified (see below).

### Turn CU (1 CU = 1 turn)

**Formula:** `spent = 1 per turn`

Tiniest possible numbers. "I did 8 chats, I owe 8 CU."

Killed immediately by the user: *"makes no sense — different turns will
be different token lengths (especially with agentic traces / chat
sessions where it keeps growing because of prefilling)."*

A 1-token aborted turn shouldn't cost the same as a 2000-token long
prompt. Discarded.

### Wall-clock time CU (1 CU = N seconds)

**Formula:** `spent = turn_wall_clock / N`

Rewards uptime. Same unit across models.

| Property | Score |
|---|---|
| Hardware-neutral | **✗ — fails.** A fast Mac finishes the same work in 1/5 the time. Time-CU would pay it 5× less for the same work. Inverted incentive. |
| Gameable | ✗ — workers can stall to claim more time |
| Determinism | ✗ — clock skew across machines |

Discarded.

### Bytes-streamed CU (1 CU = N bytes × tokens)

**Formula:** `spent = total_tokens × total_model_bytes / N`

Reflects the actual hardware bottleneck on Apple Silicon (memory
bandwidth). Cross-model fair, cross-quantization fair.

| Property | Score |
|---|---|
| Numbers | tunable via N |
| Model-aware | ✓ bytes scale with model size |
| Quantization-aware | ✓ 4-bit and 8-bit have different bytes |
| Cross-hardware | ✓ same metric on any machine |
| Reflects Mac bottleneck | ✓ |
| Reflects GPU bottleneck | ✗ — GPUs are often FLOP-bound, not bandwidth-bound |

Defensible. We considered this seriously. Ultimately lost to FLOP-CU
on the "future-proof for GPUs and MoE" axis.

### FLOP CU (1 CU = 1 TFLOP) ← winner

**Formula:** `flops_done = 2 × active_params × total_tokens`
**`spent = flops_done / 10¹²`**

The unit of compute work. Standard ML accounting.

| Property | Score |
|---|---|
| Numbers | ✓ tunable via the TFLOP definition; year of heavy use stays under 1M |
| Model-aware | ✓ active_params is model-intrinsic |
| Quantization-aware | ✗ — FLOPs are the same regardless of weight precision |
| MoE-aware | ✓ **for free** — uses `active_params` (top-k × per-expert for MoE) |
| Cross-hardware | ✓ same FLOP on M1 Pro, M2 Ultra, H100 |
| Cross-framework | ✓ MLX, llama.cpp, vLLM all converge on the same number |
| Industry-standard | ✓ FLOPs are how ML papers report compute |
| Future-proof for GPUs | ✓ |

The "quantization-blind" item is real but ultimately defended as a
*feature*: the model has an inherent compute cost; the worker's choice
of quantization is their efficiency optimization, not the pool's
problem.

---

## How the architecture clarification shaped the decision

We almost shipped Token-CU at one point. Then this exchange:

> **arpan:** wait if password isn't saved how would i recreate the same hash?
> *(unrelated, but a reminder that v1 design must survive real questions)*

> **arpan:** for reference there one 1 hub and 1 pool
> **claude:** then Token-CU is fine, single-model-per-hub assumption
> **arpan:** one hub can "host" all models — hub is just the control node, it makes decisions. it can host any model but using the worker pool. so basically the user decides what model they wanna query

That third message changed everything. A multi-model hub means:

- Alice queries Model-1 for 60 tokens
- Bob queries Model-2 for 60 tokens at the same hub
- Same ledger, same CU unit
- Token-CU would charge them the same (60 tokens each) even though Bob's
  query is ~64× more compute
- Workers serving Bob would be under-credited; workers serving Alice
  over-credited

So Token-CU was killed by the architectural reality. Cross-model
fairness moved from "future concern" to "day-1 requirement."

That left Bytes-CU and FLOP-CU. We chose FLOP-CU because:

1. **Industry-standard** — anyone in ML knows what a TFLOP is.
2. **Hardware-independent across the future expansion path** — not tied
   to Mac memory bandwidth, works the same when GPUs join.
3. **MoE-aware for free** — sparse models naturally cost less per token
   in active params, which is correct.
4. **Cross-framework neutral** — MLX, llama.cpp, vLLM all agree on FLOPs.

The quantization-blindness is the one principled cost. We absorb it:
choosing to host bf16 instead of 4-bit is the worker's efficiency
choice, and it doesn't change what the model fundamentally costs to
run.

---

## How the size/scale was picked

Once the formula was decided, the question was: what's 1 CU equal to in
FLOPs? Three options:

| Definition | Qwen-0.5B 100-token turn | Year of heavy Qwen-7B use |
|---|---|---|
| 1 CU = 1 GFLOP | 100 CU | 7.3 × 10⁹ CU |
| **1 CU = 1 TFLOP** (chosen) | **0.1 CU** | **252,000 CU** |
| 1 CU = 1 PFLOP | 0.0001 CU | 252 CU |

TFLOP gives readable numbers across the practical range:

| Scenario | FLOPs | CU |
|---|---|---|
| arpan's 488-token chat on Qwen-0.5B | 488 GFLOPs | 0.49 CU |
| 100-token chat on Qwen-7B | 1.4 TFLOPs | 1.4 CU |
| 100-token chat on Qwen-32B | 6.4 TFLOPs | 6.4 CU |
| 100-token chat on Qwen-72B | 14 TFLOPs | 14 CU |
| 100-token chat on DeepSeek-V3 (37B active) | 7.4 TFLOPs | 7.4 CU |
| Year of heavy Qwen-7B use | 252 PFLOPs | 252,000 CU |
| Year of heavy Qwen-32B use | 1.15 EFLOPs | 1.15M CU |

For exact integer math, we store **μCU** (1 μCU = 10⁻⁶ CU = 10⁶ FLOPs).
Display divides by 10⁶ at the API/CLI layer.

---

## What we deferred or accepted as limitations

### Accepted

- **Quantization-blindness.** bf16 and 4-bit cost the same CU. Worker's
  efficiency choice, not pool's problem.
- **Attention-FLOPs approximation.** The `2 × active_params × tokens`
  formula slightly underestimates long-context cost (attention is
  O(seq²)). For chat-length contexts the error is <5%.
- **Final-plan attribution on recovery.** Workers active at turn end
  get credit. Workers that died mid-turn don't get partial credit.
  Simplification.

### Deferred to follow-up plans

- **Junk-output workers earning CU for fake work** → addressed by
  `spot-check-audits` (separate plan)
- **Account impersonation** → addressed by `cryptographic-ledger`
  (separate plan)
- **Hub-operator tampering with the ledger** → addressed by
  `cryptographic-ledger` (hash-chained signed entries)
- **Resource abuse (querier with deep-negative balance)** → future
  rate-limiting plan
- **Bandwidth-aware fairness (faster workers earn more)** → optional
  refinement; would be a `fairness-aware-allocation` plan

---

## How FLOP-CU future-proofs for GPU workers

dots starts Apple-Silicon-only, but the long-term goal is **multi-backend
pools** — Macs running MLX, NVIDIA boxes running vLLM, AMD boxes running
ROCm, Linux CPUs running llama.cpp, eventually mobile devices too. FLOP-CU
is the only candidate that survives that transition unchanged. Here's
why, point by point.

### 1. FLOPs is universal across hardware classes

A single FLOP means the same thing on:

| Device | Bottleneck | Doesn't matter for FLOP-CU |
|---|---|---|
| M1 Pro / M3 Max (Apple Silicon) | memory bandwidth | ✓ — FLOPs count is identical |
| NVIDIA RTX 4090 / H100 | compute (FLOPs) | ✓ |
| AMD MI300 (ROCm) | mixed | ✓ |
| Intel CPU with AVX-512 | compute + bandwidth | ✓ |
| Modern smartphones (NPUs / GPUs) | varies | ✓ |

The Bytes-CU alternative would have been wrong here. On a 4090, the
memory bandwidth bottleneck doesn't apply — the GPU is FLOP-bound. A
bytes-streamed metric would systematically underpay GPU workers
relative to their actual compute contribution.

### 2. Mixed-precision workers integrate cleanly

GPU workers often run FP8, INT8, or even FP4 — quantization schemes that
don't exist (yet) on Apple Silicon. Under FLOP-CU:

- The model's FLOP count is **defined by the model architecture**, not
  the worker's chosen precision.
- A Mac worker running 4-bit Qwen-32B and a GPU worker running FP8
  Qwen-32B both contribute the same FLOPs per token of the layer they
  hold. They earn the same CU rate.
- The worker that picked a more efficient precision gets a *speed*
  advantage (more tok/s through their stage), but not a CU premium.

That's principled: precision is an efficiency choice; the work the
model wants done is the same.

### 3. Cross-framework neutrality

Different backends run the same forward pass in different code:

- MLX: weight matrices in Apple's MX format, GPU kernels via Metal
- vLLM: PagedAttention, FlashAttention, CUDA kernels
- llama.cpp: GGUF tensors, CPU SIMD + GPU offload
- TensorRT-LLM: NVIDIA-specific fused kernels

All of them do the same `2 × params × tokens` of forward-pass arithmetic.
**FLOP-CU is the only unit that agrees across backends without rounding
or translation.** No framework-specific multipliers needed.

### 4. Heterogeneous pools become trivial to reason about

Imagine a future pool: 2 Macs + 1 RTX 4090 + 3 CPU boxes. All running
slices of the same model. Under FLOP-CU:

- The planner assigns layers proportional to each worker's *capacity*
  (RAM + bandwidth + speed).
- Workers earn proportional to layers held.
- Different hardware tiers naturally end up with different layer
  shares, hence different CU rates per turn — without any per-hardware
  pricing logic in the ledger.

You could literally drop a new backend in (worker speaks the dots wire
protocol → done) and FLOP-CU continues to be fair.

### 5. The "worker can't choose model" principle generalizes

Same argument as for Mac-only pools: workers are assigned by the hub
to a model and a layer slice. They didn't pick either. FLOP-CU pays
them for what they actually computed (which depends on both
assignments). A GPU worker assigned to a tiny model earns less per
turn than the same worker assigned to a 70B model — and that's
correct, because they really did less compute on the tiny one.

The worker has agency over which pool to join. The pool has agency
over what model to host. CU is the honest accounting in between.

### 6. The unit doesn't need to be redefined

When GPUs join, we don't bump VERSION on the ledger, don't add a
per-hardware multiplier, don't introduce a new currency. **A μCU is a
μCU.** Macs that earned 1000 μCU yesterday and GPUs that earn 1000 μCU
tomorrow have done the same amount of work.

Bytes-CU would have required either (a) a per-hardware bandwidth
normalizer (ugly) or (b) accepting that GPU workers are perpetually
under-credited (broken).

### 7. Existing ML literature backs this up

Papers in the field (e.g., Chinchilla scaling laws, FlashAttention,
H100 launch benchmarks) all report compute in FLOPs precisely because
it's the cross-hardware unit. We're not inventing anything; we're
adopting the standard ML community has converged on for the same
reason we're picking it: it's the only metric that compares.

### What we'd still need to add later

GPU workers don't *require* changes to the CU formula. They do require
changes elsewhere in dots:

- **Protocol-first design** (separate plan: multi-backend-pool) — the
  wire format must be backend-agnostic
- **Worker-backend handshake** — workers advertise their backend
  capabilities at HELLO
- **Cross-backend numerical tolerance** — different backends produce
  slightly different bf16 rounding; spot-checks may need a tolerance
  threshold

None of those affect the ledger. The CU economy stays exactly the same.

---

## Things to revisit if the architecture changes

| If… | Reconsider |
|---|---|
| Pool grows to public/strangers | All three deferred security plans become MUSTs |
| GPUs join the pool | FLOP-CU still works; bandwidth-CU would have broken |
| MoE models become common | FLOP-CU's `active_params` handles them; no change needed |
| Cross-pool credit transfer becomes a feature | FLOP unit is universal; transfer math is straightforward |
| Sub-token billing needed (e.g., partial generations) | μCU resolution may be too coarse; consider nCU (10³ FLOPs) |
| Per-stage latency becomes a fairness factor | Layer in a bandwidth multiplier on top of FLOP-CU |

---

## The one-line summary

**1 CU = 1 TFLOP because it's the unit ML papers report compute in, it's
hardware/framework/quantization-independent, it handles MoE for free,
and it's the only candidate that survives both the single-model and the
multi-model-on-one-hub architectures unchanged.**

The cost: workers don't get a quantization premium. They chose the
quantization; the pool just charges for what the model fundamentally
costs to run.

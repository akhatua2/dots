# Resource-aware scheduling

| | |
|---|---|
| **Status** | proposed |
| **Effort** | L |
| **Complexity** | high |
| **Risk** | medium |
| **Depends on** | [[hybrid-pipeline]] (workers must be resource bags, hub off data plane) |
| **Unlocks** | efficient multi-region pools; usable big-pool latency; foundation for [[multi-backend-pool]] |
| **Branch** | (set when in-progress) |
| **PR** | (set when in-progress) |

## Why

[[hybrid-pipeline]] makes the hub a planner. This plan owns *what the hub
actually plans*. Without intelligent placement, the data-plane refactor is
hollow — pipelines get scrambled across continents, RAM-constrained workers
get over-assigned, and the scheduler always picks the wrong worker for the
job.

Three things have to come together for the placement to make sense:

1. The hub knows what each worker **has** (RAM, CPU, GPU, disk, backends).
2. The hub knows the **latency / bandwidth** between every pair of workers.
3. The hub solves a placement problem: a **constrained shortest-path** over
   the resource graph for a given work-unit DAG.

## What changes

- **`dots/protocol.py`**:
  - `hello` gains a `resources` field: `{ram_gb_free, cpu_cores, gpu_tflops,
    disk_gb_free, backends: [...], network_hints: {...}}`. Workers send this
    every reconnect; hub may request a refresh.
  - New `probe_rtt(target_id, target_addr, samples)` (hub → worker).
  - New `probe_result(target_id, rtt_ms_p50, rtt_ms_p99, bw_mbps_est)`
    (worker → hub).
- **`dots/hub.py`** gains a `Scheduler`:
  - `resources: dict[worker_id → ResourceDecl]`
  - `latency_matrix: dict[(w_a, w_b) → LatencyCell]`
  - `place(work_units, constraints) → Placement` — the optimizer.
- **`dots/work_units.py`** (new): the work-unit data model. Each unit
  declares its resource needs (RAM, compute class) and its dependencies
  in the per-token DAG (e.g. embed must precede layer_0; lm_head depends
  on layer_N).
- **`dots/worker.py`**: respond to `probe_rtt` by pinging the target via
  the same channel it uses for data; report back. Refuse probes to
  targets not previously introduced by the hub (see
  [[worker-security-model]]).

## Approach

### Worker = resource declaration

```python
@dataclass
class ResourceDecl:
    ram_gb_free: float
    cpu_cores: int
    gpu_tflops_est: float    # 0 if no GPU
    disk_gb_free: float
    backends: list[str]       # ["mlx"], ["llamacpp", "vllm"], etc.
    network_hints: dict       # { lan_subnet: "10.0.0.0/24",
                              #   tailnet: "alice.ts.net" }
```

`network_hints` informs initial probing strategy (probe within-LAN pairs
first) and gives a quick first-cut clustering before active probes complete.

### Work units

```python
@dataclass
class WorkUnit:
    id: str                   # "embed", "layer_5", "lm_head", "sample"
    model_id: str
    ram_required_gb: float
    compute_class: str        # "matmul-heavy", "lookup", "cpu-only"
    backend_required: set[str] # {"mlx"} or {} (any)
    upstream_unit: str | None # who's previous in the chain
```

For a transformer model, the unit DAG is roughly:

```
embed → layer_0 → layer_1 → ... → layer_N → final_norm → lm_head → sample
                                                                      │
                                                                      └─► (loop: embed of new token)
```

Units that talk a lot to each other (e.g. `lm_head → sample → embed` in the
inner decode loop) are tagged `chatty: true` — the placer aggressively
co-locates chatty units on one worker (0ms edge).

### Latency graph

```
Hub maintains: latency[w_a][w_b] = {
    rtt_p50_ms, rtt_p99_ms, bw_mbps_est,
    last_measured_iso, sample_count
}
```

Populated by **hub-triggered probing**:

1. On worker join: hub asks new worker to probe each existing worker AND
   asks each existing worker to probe the new one.
2. Periodic refresh: every ~5 min the hub re-probes the matrix, or marks
   cells stale and lazily refreshes when the scheduler reads them.
3. On replan trigger: refresh the pairs involved in a slow placement.

See [[worker-security-model]] for how probe requests are authenticated and
why they only target hub-introduced peers.

#### Trust mitigations on reported RTTs

Workers self-report. Three checks the hub runs on every report:

| Check | What it verifies |
|---|---|
| **Cross-report consistency** | W1's report of W2 ≈ W2's report of W1 (within tolerance). Mismatch flags both. |
| **Triangle-inequality bound** | True RTT(W1, W2) ≥ \|Hub→W1 − Hub→W2\|. The hub knows its own RTTs to each worker; anything reported below the bound is rejected as impossible. |
| **Outcome verification** | If a placement chosen on reported-fast links actually runs slow, replan and apply a reliability penalty. Continuous, passive. |

For v1, the first two are enough. Outcome verification matters for
public-pool / adversarial scenarios.

### Placement algorithm

The constrained shortest-path formulation:

```
Given:
   U  = ordered work units (DAG)
   W  = workers with resource declarations
   L  = latency matrix between workers

Find: assignment a: U → W minimizing
   Σ over edges (u_i → u_j) in U of:
      L[a(u_i)][a(u_j)]              (network cost; 0 if same worker)
   + Σ over workers w of:
      compute_time(units_on_w, w)    (compute cost on that worker)

Subject to:
   for every worker w:
      Σ over units assigned to w of ram_required_gb ≤ ram_gb_free(w)
      every unit's backend_required ⊆ w.backends
```

Two algorithms in v1, picked by pool size:

| Pool size | Algorithm | Why |
|---|---|---|
| **≤ 10 workers** | Exact: 1-D bin-packing variant with DP over the unit chain | Pools will be small for a long time; exact is simpler than a heuristic to debug |
| **> 10 workers** | Greedy: nearest-neighbor walk through the chain, bin-pack within each tier of the latency graph | Acceptable approximation; clear failure modes |
| **Future** | Integer LP / metaheuristic | When pool topology gets complex enough that greedy is visibly wrong |

### Placement policies

The placer is also configurable on policy preferences:

| Policy | Default | What it does |
|---|---|---|
| `prefer_querier_machine_for_entry` | true | If the querier is in the pool and has RAM headroom, place embed + lm_head + sample on their own machine. Solves most "head is risky" privacy/trust issues. |
| `co_locate_chatty_units` | true | Bind units tagged chatty onto one worker if any worker has the headroom. |
| `latency_tier_clustering` | true | Prefer chains within a single latency tier (LAN > tailnet > WAN). Cross-tier only when capacity forces it. |
| `replan_on_drop_threshold_seconds` | 5 | Trigger a replan if a worker fails to respond this long. |

### Replan triggers

- Worker joins (might improve placement).
- Worker leaves / drops (forces a replan).
- A cell in the latency matrix exceeds a degradation threshold.
- Per-turn outcome was slower than the placement estimated by > X%.

## Key design decisions

| Option | Decision |
|---|---|
| **Workers probe each other autonomously** | rejected — uncontrolled probe storms, harder to audit |
| **Hub triggers all probes** | **chosen** — hub controls cadence, pair selection, and timing |

| Option | Decision |
|---|---|
| **Hub measures only worker-to-hub RTT, infers W↔W by triangle inequality** | rejected — triangle inequality is too loose (LAN peers both 100ms from hub could be 1ms apart) |
| **Workers measure pairwise, report to hub** | **chosen** — accurate; only honest workers required for accuracy; cross-check + bounds for the dishonest case |

| Option | Decision |
|---|---|
| **Exact ILP solver for placement** | overkill for v1; defer |
| **DP for small pools, greedy for larger** | **chosen** — covers v1 + early growth |
| **Random / round-robin placement** | rejected — wastes the whole point of having a resource graph |

| Option | Decision |
|---|---|
| **Probe RTT only** | rejected — bandwidth matters too for prefill |
| **Probe RTT + occasional bandwidth** | **chosen** — RTT every probe cycle, bandwidth on join or on demand |
| **Full network performance benchmarks** | overkill; defer |

| Option | Decision |
|---|---|
| **Refresh entire matrix periodically** | **chosen** for small pools |
| **Lazy refresh only stale cells the scheduler reads** | **chosen** for large pools; default switches at threshold |

## Open questions

- **How aggressive should outcome verification be?** Trade off: too sensitive
  and good workers get penalized for noise; too lax and bad reports go
  uncaught. Start conservative (penalty only on > 3σ outliers).
- **Bandwidth estimation cost.** A real bandwidth probe is bytes on the
  wire. How often is acceptable? Lean: on join only, plus opportunistic
  from real workload traffic.
- **Heterogeneous compute time estimates.** Two workers with identical RAM
  may have very different per-FLOP times. We need a compute-time model per
  worker; learned from real turn outcomes vs estimated from a benchmark
  unit. Lean: ship with a startup benchmark, refine from real data.
- **Multi-tenant placement.** When N concurrent queries are running on the
  same pool, the placer needs to consider current resource use, not just
  free RAM at start. Lean: track in-flight commitments per worker.
- **Sticky placement across turns.** Long conversations benefit from
  placement stability (KV cache reuse). The placer should weight "keep
  same workers as last turn" when feasible. Lean: yes, with a clear
  override when topology changes meaningfully.

## Test plan

- **Unit: placer correctness.** Synthetic worker graphs + resource decls
  + unit DAGs → assert resulting placement satisfies all constraints
  and is the minimum cost achievable for the algorithm tier.
- **Unit: triangle-inequality rejection.** Synthetic reports that violate
  the hub's bound are rejected.
- **Unit: cross-report mismatch flag.** W1's report disagrees with W2's;
  scheduler logs both and weights placements away from the disputed pair.
- **Integration: probe protocol.** Hub-triggered probe round-trips
  observed on workers; reports arrive at hub.
- **End-to-end: latency-aware chain.** Two-tier pool (some workers LAN,
  some on tailnet 80ms). Chain stays within the LAN tier if it can fit.
- **End-to-end: capacity-bound spillover.** Pool with one worker too
  small; chain spills to the next tier; cost reflects the longer path.
- **Fuzz: random worker arrival/departure.** Placement converges; no
  infinite-replan loops.

## Out of scope

- **Speculative pre-placement / model warm-up.** Useful but separate.
- **Cross-pool placement.** This plan is single-pool.
- **Failure-aware redundant placement.** No K-of-N duplication in v1.
- **Worker quality-of-service contracts.** No SLAs; reliability is observed
  and weighted, not negotiated.

## Followups

- **Reputation scoring** (future plan) — formal account-level reliability,
  fed by outcome verification + churn rate + cross-report consistency.
- **Spot-check audits** (future plan) — hub re-runs ~1% of computations
  on a different worker. Catches workers that report correct RTT but
  return junk output.
- [[multi-backend-pool]] — backend_required in WorkUnit gets richer once
  multiple backends ship; placement just needs to honor more constraints.
- [[cryptographic-ledger]] — signed probe reports make outcome
  verification far stronger.

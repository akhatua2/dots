# Dynamic Simulation Test Environment

`tests/simulation/` is for deterministic full-pool simulations. These tests
mock time, workers, users, network, disk, memory, model preparation, turns,
and ledger events. They do not import MLX, call Hugging Face, download model
bytes, touch real disk cache, or sleep.

The purpose is decision testing:

- prove resource constraints are never violated;
- find dominated or unsafe serving choices;
- compare conservative production choices against explicit scenario metrics;
- reproduce join/leave/cache/network/disk edge cases exactly.

## Time Model

Simulation time is virtual. One simulated hour can run in milliseconds because
`SimClock` jumps from one scheduled event to the next.

No test should use real `sleep`.

## Resource Model

Workers explicitly declare:

- RAM and disk budget;
- download throughput from the model source;
- disk read/write throughput;
- memory bandwidth;
- compute throughput;
- backend setup time;
- backend conversion throughput;
- supported backend set;
- owner account and optional owner user;
- location/network cluster;
- hub and peer latency inputs.

Users explicitly declare:

- account id;
- location/network cluster;
- client-to-hub RTT;
- TPS, TTFT, and CU satisfaction targets.

The main large scenario models a week of people, not anonymous workers. Chat
users and compute workers are different resources:

- a user can be chat-only and only consume capacity;
- a user can own one or more workers under the same account;
- provider-only workers can contribute capacity without generating chat demand.

Users and workers can join, leave, rejoin, and leave again across the same
week. Personas drive their calendars: workday builders, evening hobbyists,
weekend hobbyists, research labs, and batch automation.

Models explicitly declare model facts:

- repository and architecture;
- dense or MoE shape;
- total and active parameters;
- layer count, hidden size, and context length.

Backend artifact snapshots are `dots.backends.artifacts.BackendArtifactProfile`
instances. They explicitly declare runtime packaging facts:

- source artifact byte size from a pinned metadata snapshot;
- prepared backend artifact size when conversion is modeled;
- conversion scratch size when conversion is modeled;
- loaded RAM footprint;
- wire dtype.

Simulated "download" phases are virtual transfers only:
`artifact_size / worker.download_gb_per_s`. They never perform network IO.

Production Dots owns the profile math and serving-policy choices. Simulation
owns only deterministic inputs: pinned model metadata, pinned artifact metadata,
worker speeds, and latency meshes.

## Network Model

The production scheduler consumes a worker-to-worker `LatencyMatrix`, so the
simulator builds a fully connected peer mesh for every worker. Locations are
clustered to produce realistic spread:

- same campus/datacenter links are sub-2ms to low-single-digit ms;
- same metro links are low-ms;
- same-region links are tens of ms;
- cross-continent links are 100ms+.

Chat-only users contribute client-to-hub RTT. If a user owns a worker, that
machine also participates in the worker-to-worker mesh and self-utilization is
represented by the shared account id.

## Traffic Model

The default large run is a one-week peak-traffic simulation. It includes:

- zero-traffic quiet phases, such as overnight windows;
- normal weekday work demand;
- evening hobbyist demand;
- weekend hobbyist peaks;
- explicit overload windows at 500% nominal demand.

Demand above available capacity is represented by saturated worker busy time,
retries/waits, lower satisfaction, and abandoned/skipped requests where
appropriate. Utilization still caps at 100%; overload is demand pressure, not
impossible physical utilization.

## Incident Model

The default large run also has an incident registry. Each incident has a label,
start time, end time, optional start handler, optional finish handler, and
shared state. Incidents can overlap; adding a new incident should mean adding
one `SimIncident` entry plus a focused handler.

Current week incidents:

- `many_small_workers_huge_model`: Kimi-sized artifact constraints reject small workers.
- `network_partition`: one lab location disconnects and rejoins.
- `slow_disk_death`: selected provider disks degrade for half a day.
- `partial_cache_corruption`: one cached artifact is invalidated.
- `hot_model_no_free_ram`: background local memory pressure reduces replica headroom.
- `asymmetric_network`: one peer direction becomes slow while the reverse stays fast.
- `competing_model_priorities`: huge, MoE, and medium models compete in the same window.
- `model_upgrade_swap`: traffic shifts from one model family to a replacement model.
- `whale_worker_churn`: a very large provider joins, attracts work, then leaves.
- `medium_model_flash_crowd`: Qwen2.5-14B receives overload traffic and should trigger replicas.
- `provider_fairness_concentration`: final ledger concentration is reported.

## Load Phases

Cold model preparation is modeled as ordered phases:

1. `backend_setup`
2. `source_virtual_download`
3. `source_cache_write`
4. `source_read_for_conversion`
5. `artifact_conversion`
6. `prepared_artifact_write`
7. `weight_load_read`

Cache state skips phases deterministically:

- installed backend skips setup;
- cached source skips download and source write;
- prepared artifact skips conversion and prepared write;
- loaded model skips all load work.

## Metrics

Every scenario should be able to report:

- pool RAM utilization;
- pool disk utilization;
- per-worker effective TPS;
- active worker utilization;
- model queue pressure;
- ready replicas that never served a turn;
- replica imbalance across ready workers;
- user turn satisfaction;
- CU spent/earned ledger balances;
- event log with exact simulated timestamps.

The main dynamic run records:

- repeated user and worker joins/leaves across a simulated week;
- user and worker leaves;
- request arrivals over simulated time;
- cold virtual loads and cache hits;
- pressure-triggered replicas under sustained demand;
- cache reaper passes that evict stale artifacts under disk pressure;
- waits for active loads or capacity;
- reuse of already-loaded deployments;
- skipped requests when a user has left;
- mid-turn failures when a worker leaves.

## Production Policy

The simulator should not define a separate serving policy. It may model virtual
physics, but serving choices call production code in `dots.serving.policy`.
That module currently owns:

- ready-replica selection;
- pressure-replica gating;
- load-worker selection under RAM, disk, backend, cache, and active-load constraints.

Future production planner code should own candidate actions such as:

- reuse existing deployment;
- add full replica;
- add shard;
- rebalance;
- wait/reject.

Those production candidates can be scored using simulator-generated metrics:

- user TPS;
- user CU cost;
- pool headroom;
- disk headroom;
- reload GB;
- load seconds;
- disrupted active turns;
- fairness delta.

This is not a true oracle, especially for large dynamic pools. Small scenarios
can be exhaustively enumerated later inside production planner tests, but large
simulation scenarios should emit metrics and assert hard invariants. Production
does not need to be optimal today. Simulation tests should make it easy to see
when production is safe but conservative versus unsafe.

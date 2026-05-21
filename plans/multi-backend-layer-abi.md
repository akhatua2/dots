# Multi-Backend Layer ABI

| | |
|---|---|
| **Status** | proposed |
| **Effort** | XL |
| **Complexity** | high |
| **Risk** | high |
| **Depends on** | [[hybrid-pipeline]], [[resource-scheduling]], [[partial-weight-loading]] |
| **Unlocks** | Mixed backend worker pools, CPU workers, future vLLM/llama.cpp backends |
| **Branch** | |
| **PR** | |

## Why

Dots should let users offer whatever machines they have without making them
choose the execution stack. The hub should select a compatible execution plan
from worker capabilities, model requirements, and resource limits.

Today the only implemented execution backend is MLX. We should keep the
current production rule simple: one model execution chain uses one backend
family. Cross-backend layer splitting is possible only after Dots owns a
precise hidden-state and cache ABI, not because every runtime can do matrix
multiplication.

## What changes

- Add a backend setup lifecycle before normal worker HELLO readiness:
  `probe -> install/verify -> sandbox profile -> capability declaration`.
- Add a `BackendBootstrap` contract under `dots/backends/base.py`.
- Add per-platform runner/sandbox adapters under `dots/system/runner/`.
- Extend `BackendCapabilities` with model architecture, artifact format,
  hidden-state ABI, KV-cache ABI, dtype, quantization, tokenizer/template,
  device type, setup status, and sandbox policy.
- Extend `ModelAssignment` to target model components, not only numeric
  transformer layer ranges.
- Add conformance tests that compare backend segment output against a
  reference for a fixed prompt, seed, dtype policy, and model artifact.
- Teach placement to build compatibility groups and choose one group per
  production placement.

## Approach

Phase 1 is what the refactor is doing now: workers advertise implemented
backend capabilities, the hub selects one backend per placement, and workers
load only the backend named by the hub. MLX is the only implemented backend.
llama.cpp/vLLM are represented as future capability names, not silently used.

Phase 2 adds worker bootstrap. A backend can declare setup requirements such
as Python wheels, native libraries, model converters, disk cache paths, and
runner requirements. Linux sandboxing can use bubblewrap or a compatible OCI
runner when available. macOS needs a separate runner policy because there is
no drop-in bubblewrap equivalent; viable options are a locked-down subprocess
profile, a dedicated venv plus resource limits, or a VM/container boundary for
untrusted code. The hub never installs libraries itself.

Phase 3 adds a Dots Layer ABI. A segment boundary must define tensor shape,
layout, dtype, byte order, RoPE position semantics, attention mask semantics,
cache ownership, residual/norm placement, tokenizer/template identity, and
acceptable numerical tolerance. Backends can mix in one chain only when both
sides pass conformance for the exact model architecture and artifact.

Phase 4 allows experimental cross-backend placements behind an explicit
compatibility gate. The default remains same-backend chains until there are
model-specific tests proving the mixed path is correct and stable enough.

## Key design decisions

| Option | Why considered | Decision |
|---|---|---|
| Treat backends as interchangeable matmul engines | Simple mental model | Rejected. Runtime differences include cache layout, quantization, RoPE, normalization, tensor layout, and tokenizer behavior. |
| Let each worker pick its backend | Easy local autonomy | Rejected. The hub owns placement and must pick a compatible backend group for the whole chain. |
| Default every worker to all known backends | Makes future names visible | Rejected. Workers advertise only implemented, verified capabilities. |
| One backend per production chain | Correct and testable now | Chosen for current production behavior. |
| Mixed backends with conformance gates | Enables research later | Chosen as future work after the ABI exists. |

## Open questions

- Which CPU backend ships first: llama.cpp Python bindings, direct
  llama.cpp server/process control, or another runtime?
- What macOS isolation boundary is acceptable for backend setup work that
  may compile, convert, or download artifacts?
- How strict should numerical tolerances be for quantized cross-backend
  segment comparisons?
- Should prepared artifacts be hosted by Dots later, or always generated on
  workers from upstream model checkpoints?

## Test plan

- Unit-test worker bootstrap capability declarations for installed,
  missing, and setup-needed backends.
- Integration-test hub selection with mixed workers: MLX-only, CPU-only,
  and incompatible mixed pools.
- Add conformance fixtures per model architecture and backend pair before
  enabling cross-backend routing.
- Verify resource limits cover setup, conversion, and loading: disk, RAM,
  CPU, read I/O, write I/O.
- Verify stale prepared artifacts are evicted before exceeding worker disk
  budget.

## Out of scope

- No cross-backend production routing in the current refactor.
- No llama.cpp or vLLM implementation in the current refactor.
- No automatic dependency installation without an explicit worker bootstrap
  policy and sandbox runner.

## Followups

- [[resource-scheduling]] should consume richer backend capability groups.
- Add a dedicated worker bootstrap plan once we choose the first CPU backend.
- Add model cache accounting for prepared artifacts, conversion scratch space,
  and eviction telemetry.

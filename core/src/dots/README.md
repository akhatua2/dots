# Dots Package Map

Dots is organized around architecture boundaries, not implementation
details.

- `app/`: user-facing API and chat UI.
- `distributed/`: hub, worker, peer connections, pool state, and wire
  message flow.
- `backends/`: backend runtimes. Backend-specific code, conversion,
  model loading, tensor encoding, token sampling, and framework imports
  live here.
- `generation/`: token generation strategies over the backend runtime
  interface.
- `placement/`: backend-neutral model metadata, resource scheduling,
  sharding strategy selection, and work-unit descriptions.
- `serving/`: side-effect-free model-serving policy: replica selection,
  pressure-replica gating, and load-target choice.
- `compute_plane/`: planning/routing abstractions. This layer must not
  know model formats or backend internals.
- `accounting/`: compute-unit ledger.
- `system/`: local machine resource discovery and configuration helpers.

Top-level modules such as `dots.worker` and `dots.scheduler` are
compatibility shims for older imports. New code should import from the
architecture package directly.

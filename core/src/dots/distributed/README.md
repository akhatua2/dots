# Distributed

Hub/worker distributed runtime.

- `hub.py`: orchestrates pool membership, placement, prompts, token
  routing, readiness, and accounting.
- `worker.py`: connects to the hub, owns a selected backend runtime, and
  handles delegated turns and peer compute calls.
- `peer.py` and `protocol.py`: generic RPC framing.
- `pool.py`: hub-side resource and latency state.

Distributed code should speak Dots messages and backend interfaces. It
should not import backend frameworks directly.

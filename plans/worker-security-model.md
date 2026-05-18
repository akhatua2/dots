# Worker security model

| | |
|---|---|
| **Status** | proposed |
| **Effort** | M |
| **Complexity** | medium |
| **Risk** | medium |
| **Depends on** | [[hybrid-pipeline]] (peer-to-peer connections introduce the new attack surface) |
| **Unlocks** | safe public dots pool; basis for [[cryptographic-ledger]] signature schemes |
| **Branch** | (set when in-progress) |
| **PR** | (set when in-progress) |

## Why

Today the worker only talks to its hub, and the hub is the user's own machine.
The trust model is implicit: worker trusts hub, no third party in scope.

[[hybrid-pipeline]] breaks that. Workers will talk directly to other workers
to pass hidden states, and the hub may be a public node operated by someone
else. The worker process is now exposed to two new parties — peer workers
and a potentially adversarial hub — without any formal scope of what either
can ask it to do.

Without an explicit security model, dots is one bad participant away from:
turning workers into open relays, leaking user prompts to peers, accepting
arbitrary code from a compromised hub, or being used as a port-scanning
proxy. Each of these has a specific mitigation; together they form the
worker security model.

## What changes

- **`dots/worker.py`** — split message handler tables: `_handle_hub_msg` vs
  `_handle_peer_msg`. The two tables share zero entries; control-plane ops
  are hub-only by construction.
- **`dots/protocol.py`** — peer-to-peer messages must carry a `session_token`
  field issued by the hub when peers are introduced.
- **`dots/worker.py`** — outgoing connection list maintained in memory:
  `authorized_peers: dict[peer_id → (addr, session_token, valid_until)]`.
  Anything not in the list is rejected at handshake.
- **`dots/worker.py`** — bind address defaults to LAN-only / overlay IP.
  Never `0.0.0.0` unless explicitly configured.
- **`dots/worker.py`** — per-peer rate limits (probes, compute requests,
  payload size) enforced before any handler runs.

## Approach

### Threat model

The model treats four parties as untrusted by default:

1. **Untrusted hub** — for public pools, the hub may be run by someone
   other than the user. Goal: prevent hub from using the worker as a
   shell, scanner, or relay.
2. **Untrusted peer** — another worker in the same pool. Goal: prevent
   peer from issuing commands outside its scope (e.g. asking the worker
   to load a different model, exfiltrate state).
3. **Network attacker** — someone sniffing or injecting traffic on the
   wire. Goal: encrypted channels; signed messages where feasible.
4. **External scanner** — random internet probing. Goal: not reachable
   at all without overlay credentials.

Out of scope: malicious user *on* the worker's machine. If they have
root on your Mac, the dots worker is the least of your problems.

### Hub → Worker permission surface

A small, declared API. The worker process literally has no code path for
anything outside this list. The hub cannot inject behavior via the wire.

| Op | What it does | Side effects |
|---|---|---|
| `load_units(specs)` | Load these work units (from a hub-authorized model) | Disk read of model weights; RAM allocation |
| `unload_units(ids)` | Free units | RAM release |
| `probe_peer(peer_id)` | Measure RTT to a hub-introduced peer | One small ping over WS |
| `introduce_peers(list)` | Add peers to the authorized list, with session tokens | In-memory state only |
| `compute_unit(conv_id, unit_id, inputs)` | Run a single work unit (also accessible via peer channel — see below) | CPU/GPU compute |
| `reset(conv_id)` | Clear conversation state | RAM release |
| `shutdown()` | Graceful exit | Process exit |

What this surface deliberately omits: arbitrary file access, arbitrary
network operations, code execution, system inspection, child process
spawn. None of these are reachable through the protocol.

### Per-operation permission scopes

Each op enforces an inner permission check before doing anything:

| Op | Inner check |
|---|---|
| `load_units` | Model must match the worker's configured allowlist (default: `mlx-community/*` on HuggingFace; user-tightenable). |
| `probe_peer` | Target peer must be in `authorized_peers`. Never probe an arbitrary address — the worker is not a port scanner. |
| `introduce_peers` | Only the hub can introduce peers. The hub is identified by the worker's locally-stored hub URL; peer connections claiming to introduce other peers are dropped. |
| `compute_unit` | Conversation must have a plan the worker is part of, issued by the hub. Unit ID must be one of the units the worker holds. Inputs must fit a declared shape/size. |

### Peer → Worker scope split

Peers can only call `compute_unit`. Everything else is hub-only.

```
Two handler tables, hard-separated:

  _hub_handlers      = { load_units, unload_units, probe_peer,
                         introduce_peers, compute_unit,
                         reset, shutdown }

  _peer_handlers     = { compute_unit }
```

A peer connection cannot reach `_hub_handlers` no matter what fields it
sends. The dispatcher routes by connection origin (which is established
at handshake).

### Peer authentication

When the hub introduces peers, it sends a session token scoped per pair
per conversation:

```
hub → W1: introduce_peers([
  { peer_id: W2,
    addr: 10.0.0.2:5555,
    session_token: <opaque>,
    scope: {
      conv_id: "conv-abc-123",
      valid_until: 2026-05-17T12:00:00Z,
      allowed_unit_ids: ["layer_8", "layer_9", ...] }
  }
])
```

When W1 connects to W2, the handshake includes the token. W2 verifies:

- Token format and signature (hub's session key, ed25519 over a canonical
  representation of the scope dict).
- Scope still valid (within `valid_until`).
- The conversation is one W2 currently participates in.

Mismatch on any field → reject with a clear error.

When [[cryptographic-ledger]] lands, session tokens get replaced (or
augmented) by per-message signatures over the account's identity key.
That's a stronger guarantee — no shared secret to leak.

### Network surface

| Default | Why |
|---|---|
| **Bind LAN-only** (`192.168.x.x`, `10.x.x.x`, or overlay IP) | Workers should not be reachable from the public internet. |
| **No `0.0.0.0` without explicit flag** | If a user binds wide-open, they get a startup warning and a config-acknowledgement requirement. |
| **For cross-network pools: overlay required** | Tailscale, WireGuard, or future embedded mesh. The overlay's encryption + authenticated endpoints form the outer security boundary. |
| **Hub may be internet-exposed** | Hub is a server, fine. Workers are not. |

### Rate limits and payload caps

Per-peer and per-hub limits enforced before the handler runs:

| Limit | Default |
|---|---|
| `probe_peer` requests | 10 per 60s per origin |
| `compute_unit` requests | bounded by conversation plan; unscheduled = drop |
| Per-message payload size | 100 MB default (covers a 4K context hidden state); reject oversized |
| Total inbound bandwidth | soft cap configurable; logs warnings before hard-limit |

### Process-level defense

These are defense-in-depth recommendations the worker setup applies by
default:

- Run as a non-privileged user. The dots installer never asks for sudo.
- Filesystem access scoped to `~/.dots/`, `~/.cache/huggingface/`, and
  the model cache. Anything else is gratuitous.
- macOS: `sandbox-exec` profile (or proper `App Sandbox` if shipped as
  a `.app`).
- Linux: optional seccomp filter installed at startup; user can opt out.
- No outbound network traffic except to (a) the configured hub URL,
  (b) authorized peers, (c) HuggingFace for model downloads. Anything
  else is logged + dropped.

## Key design decisions

| Option | Decision |
|---|---|
| **Single shared message handler** | rejected — easy to leak control-plane access to peers by accident |
| **Two-table split (hub vs peer)** | **chosen** — no peer message can possibly reach a hub-only op |

| Option | Decision |
|---|---|
| **Unauthenticated peer connections** | rejected — anyone on the overlay could send work |
| **Hub-issued opaque session tokens** | **chosen** for v1 — simple and effective |
| **Per-message Ed25519 signatures** | deferred to [[cryptographic-ledger]] |

| Option | Decision |
|---|---|
| **Bind `0.0.0.0` by default** | rejected — too risky |
| **LAN-only by default with explicit opt-out for wider bind** | **chosen** |

| Option | Decision |
|---|---|
| **Sandbox the worker process** | recommended, off by default — installer can opt-in |
| **No sandboxing** | rejected for public-pool use — but acceptable for private friend pools |

| Option | Decision |
|---|---|
| **Allowlist models the worker can load** | **chosen** — prevents a malicious hub from making the worker download arbitrary blobs |
| **Open model loading** | rejected — too easy to abuse |

## Open questions

- **Session token revocation.** If a session token leaks, how does the
  hub revoke it before `valid_until`? Lean: short TTL (e.g. 5 min) +
  refresh. Avoids needing a revocation list.
- **Hub identity verification by worker.** How does the worker know it's
  talking to the right hub vs an impostor? Lean: hub URL pinned on first
  setup; TOFU (trust on first use) with optional Ed25519 hub key.
- **Inbound bandwidth caps in practice.** Hidden states can be 10s of
  MB per token at very long context. Need a model-aware default, not a
  fixed number.
- **What to do when a peer reports a bad session token.** Lean: drop
  the connection silently. Don't tell the attacker which field was
  wrong (no error-channel oracle).
- **Cross-platform sandboxing parity.** macOS sandbox-exec + Linux
  seccomp diverge in expressiveness. Lean: shipped only when both have
  a tested policy; user-opt-in until then.

## Test plan

- **Unit: peer cannot call hub-only ops.** Construct a peer connection,
  send `load_units`, `shutdown`, `reset`, etc. Each is rejected at
  dispatch, not at the handler.
- **Unit: invalid session tokens rejected.** Tampered token, expired
  token, wrong-scope token → all rejected.
- **Unit: probe to non-introduced address rejected.** Send `probe_peer`
  with an arbitrary IP not in the authorized list. Rejected.
- **Unit: model allowlist enforcement.** `load_units` for a model not
  on the allowlist → rejected with clear error.
- **Integration: bind defaults LAN-only.** Worker started without
  config does not accept connections from public-internet ranges (use
  loopback test with a non-local destination IP).
- **Integration: rate limits.** Spam probe requests; verify circuit
  trips at the configured rate.
- **Integration: clean shutdown via hub.** Hub `shutdown()` → worker
  exits cleanly. Peer attempting same → ignored.
- **Adversarial: malicious hub script.** Mock hub sends a sequence of
  arbitrary ops (`exec`, `os.system` framed as `compute_unit`, model
  swap attempts). All result in either drops or in-scope behavior
  with no escalation.
- **Adversarial: malicious peer.** Mock peer attempts every hub op via
  the peer channel. Every one is rejected.

## Out of scope

- **End-to-end encryption of user prompts.** Hidden states still flow
  in cleartext between trusted (overlay-authenticated) peers. FHE-style
  privacy is a far-future plan.
- **Full app sandboxing on macOS via `.app` bundle.** When dots ships
  as a notarized Mac app this becomes natural; until then,
  `sandbox-exec` is the user-opt-in path.
- **Hardware attestation.** No TPM, no SGX, no remote attestation in v1.
- **Per-account behavioral reputation.** That's a separate
  reputation-scoring plan, downstream of this one.

## Followups

- **Reputation scoring** (future plan) — adds per-account reliability
  derived from probe consistency + outcome verification + churn rate.
- [[cryptographic-ledger]] — upgrades session tokens to Ed25519
  signatures; closes the rest of the trust gap.
- **Spot-check audits** (future plan) — catches workers that pass all
  protocol checks but return junk output. The protocol-level model here
  is necessary but not sufficient for that class of cheating.
- **Sandboxing as installer default** — once both macOS and Linux
  policies are tested, switch from opt-in to opt-out.

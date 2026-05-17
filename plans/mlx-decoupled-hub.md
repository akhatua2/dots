# MLX-decoupled hub

| | |
|---|---|
| **Status** | proposed |
| **Effort** | S |
| **Complexity** | medium |
| **Risk** | medium |
| **Depends on** | [[partial-weight-loading]] (shared infra) |
| **Unlocks** | [[cloud-hub-deploy]], [[multi-backend-pool]] |

## Why

The hub is currently bound to Apple Silicon because it uses `mlx_lm.load()`
to access `embed_tokens`, `model.norm`, and `lm_head`. None of those
operations actually require MLX — embedding lookup is a table index, lm_head
is a single matmul, sampling is numpy.

If the hub doesn't need MLX, it can run anywhere — including a free Linux
VM. That unlocks $0/month always-on hubs.

## What changes

- `dots/hub.py:Hub._load_model` — replaced with a hub-only model loader that
  uses safetensors directly (or mlx-lm in MLX-mode if available, gated by a
  config flag) and does embed / lm_head / sample with `numpy` or `torch.cpu`.
- New `dots/hub_backend.py` — small module: `embed_lookup(token_id)`,
  `final_projection(hidden) -> logits`, `sample(logits, temperature)`.
  Pure numpy implementation by default.
- `pyproject.toml` — `mlx` becomes optional (`extras = ["mlx"]`). Hub on
  Linux installs without it; workers on Mac still need it.

## Approach

The hub does three model-touching operations per token:

1. **Embed input token**: look up `embed_tokens.weight[token_id]`. A table
   index. Trivial in numpy.
2. **Final norm + lm_head**: `lm_head.weight @ norm(hidden) + bias`. One
   matmul against `[vocab × hidden]`. For Qwen-32B: 152k × 5120 × 4 bytes
   = 3 GB if fp32, 1.5 GB if fp16. Cheap on CPU.
3. **Sample**: argmax for greedy, or temperature-softmax-multinomial. Both
   pure numpy.

For *quantized* weights this is slightly more involved because we have to
dequantize before the matmul. dequant is cheap (a multiply + offset). The
matmul is the cost, and matmuls scale fine on CPU at this size.

End state: the hub binary depends only on `numpy`, `safetensors`, `aiohttp`,
`msgpack`, and `huggingface_hub`. All available on any platform.

```
                  before                              after
   ┌───────────────────────────┐         ┌───────────────────────────┐
   │ hub (must run on macOS)   │         │ hub (any Linux / Mac)     │
   │   mlx, mlx-lm, MLX runtime│         │   numpy, safetensors      │
   │   full model in RAM       │         │   tiny: embed+norm+lm_head│
   │   ~16 GB for 32B-4bit     │         │   ~1.5 GB for 32B         │
   └───────────────────────────┘         └───────────────────────────┘
```

This builds on [[partial-weight-loading]] — both refactors touch the same
loader path. Ship them together or back-to-back.

## Key design decisions

| Option | Decision |
|---|---|
| **Pure numpy implementation** | **chosen** — minimum deps, works everywhere |
| **PyTorch (CPU)** | considered — heavier dep, marginal perf win |
| **Run a llama.cpp subprocess for hub-side work** | considered — see [[multi-backend-pool]] for full discussion; defer |

| Option | Decision |
|---|---|
| **Hub auto-detects best backend** | rejected for v1 — complex; numpy is enough |
| **Hub explicit backend via `--backend numpy/mlx/llamacpp`** | **chosen** — explicit, predictable |

| Option | Decision |
|---|---|
| **Remove MLX from `[dependencies]` entirely** | rejected — workers need it |
| **Make MLX an optional `[mlx]` extra** | **chosen** — `pip install dots` works on Linux; `pip install "dots[mlx]"` for Mac workers |

## Open questions

- **Numerical equivalence with MLX hub** — pure-numpy hub and MLX hub
  should produce identical logits given identical hidden state inputs. Test
  this on a known prompt with both backends; assert max difference < 1e-4.
- **Sampling determinism across backends** — numpy and MLX use different
  RNGs. For deterministic sampling, ship a seed in the request and let the
  hub use numpy regardless of backend.
- **Loading speed.** 1.5 GB of lm_head + embed weights still loads from disk
  on hub startup. ~5 seconds. Fine.
- **Quantized lm_head support.** The default 4-bit Qwen has a quantized
  lm_head. Need a numpy quantize/dequantize helper. ~50 lines.

## Test plan

- **Logit equivalence** test: same hidden state into MLX hub vs numpy hub →
  argmax matches, top-k matches, full logits within tolerance.
- **Sampling reproducibility**: with a fixed seed, both backends produce the
  same token.
- **Linux smoke test**: spin up dots hub in a Docker container with no MLX
  installed; verify it loads and serves.
- **End-to-end on Linux**: hub on Linux VM, workers on Mac, real chat turn,
  output matches all-MLX baseline.

## Out of scope

- Multi-backend workers (separate plan: [[multi-backend-pool]]).
- C/C++ hub binary (could come much later if Python overhead becomes an
  issue; it won't, given how rarely the hub does compute).
- Hub-side model auto-quantization.

## Followups

- [[cloud-hub-deploy]] — actually use this on Oracle Cloud Free.
- [[multi-backend-pool]] — once hub is backend-agnostic, formalize the
  worker protocol so multiple backends can plug in there too.

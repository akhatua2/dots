# Partial weight loading

| | |
|---|---|
| **Status** | proposed |
| **Effort** | S |
| **Complexity** | medium |
| **Risk** | medium |
| **Depends on** | none |
| **Unlocks** | 70B-class models on heterogeneous pools, [[mlx-decoupled-hub]] (shared infra) |

## Why

Workers today load the **full model** into RAM via `mlx_lm.load()`, then free
unowned layers via `_DroppedModule` in `dots/shard.py`. Peak RAM during load
equals full model size, even though only a fraction of the layers will be
kept. A 16 GB Mac cannot load Qwen-32B-4bit (~17 GB) even to host 3 of 64
layers.

Effect: the smallest Mac in the pool caps the largest model the pool can
host, regardless of how much of that model each Mac actually retains.

## What changes

- `dots/shard.py:Shard.__init__` — replaces `mlx_load(model_id)` with a
  partial loader.
- New `dots/partial_loader.py` — opens HF cache, reads safetensors index,
  selects owned tensor names, mmaps the right shard files, constructs an
  mlx-lm model with only owned layers populated and unowned layers replaced
  by `_DroppedModule()` *before* loading any weights.
- `dots/hub.py:Hub._load_model` — on the hub side, also use the partial
  loader but for a different subset: just `embed_tokens`, `model.norm`,
  `lm_head`. No transformer layers.

## Approach

The download path stays the same — `huggingface_hub.snapshot_download` pulls
the entire model. **We keep the full model on disk** because pool rebalancing
can re-assign us any layer range, and we don't want to re-download.

The new path is during *load*: only the bytes we own ever enter RAM.

```
disk: ~/.cache/huggingface/.../model-*.safetensors  (full, ~16 GB for 32B-4bit)
                       │
                       │  read only assigned tensors via safetensors mmap
                       ▼
RAM:   layer_start..layer_end weights  (e.g. 2.5 GB for 3-of-24 layer share)
       + small constant per Mac
```

Steps in `load_partial()`:

1. Locate the model directory via `huggingface_hub.snapshot_download(...)`.
2. Read `config.json`, `model.safetensors.index.json`.
3. Compute the set of *owned* tensor names:
   - For each layer `i` in `[layer_start, layer_end)`: all tensors prefixed
     `model.layers.<i>.` (including quantization scales, biases).
   - If hub: `model.embed_tokens.*`, `model.norm.*`, `lm_head.*`.
4. Construct the model class via mlx-lm's dispatch
   (`mlx_lm.utils.get_model_classes(config)`), but **replace unowned
   submodules with `_DroppedModule` instances before any weight load**.
5. Open each safetensors shard with `safetensors.safe_open`; iterate owned
   tensors; `model.load_weights([(name, value)], strict=False)`.
6. Apply quantization (if `config["quantization"]`) — reuse mlx-lm's
   helper rather than reimplementing.

Quantized models add one wrinkle: a quantized linear is *three* tensors per
layer per linear (`...weight`, `...scales`, `...biases`). All three must
be loaded together. The owned-tensor-name set accounts for this.

## Key design decisions

| Option | Decision |
|---|---|
| **Skip full mlx-lm load by reading safetensors directly** | **chosen** |
| **Fork mlx-lm with a partial-load API** | considered — defer to upstream PR after our version is proven |
| **Keep full model loaded, just dispatch lazily** | rejected — same peak RAM as today |

| Option | Decision |
|---|---|
| **Re-download on every shard reload** | rejected — re-planning happens often, network would dominate |
| **Cache full model on disk, partial-load on demand** | **chosen** |

| Option | Decision |
|---|---|
| **Tear-down + rebuild on re-plan** | **chosen for v1** — simple, fast (SSD read, seconds) |
| **Incremental add/drop layers on re-plan** | considered — optimization for v2 |

## Open questions

- **Architecture support.** Initial scope: Qwen2.5 family (because it's the
  default). Llama, Mistral, Phi, Gemma should also work since they use the
  same `model.layers.N.<...>` naming; we'll validate per-architecture.
- **Tied weights.** Some models tie `embed_tokens.weight` and `lm_head.weight`.
  Hub needs both views; workers need neither. Detected from `config`.
- **mlx-lm version pin.** We reach into `mlx_lm.utils` internals. Pin a known-
  working version in `pyproject.toml`; bump deliberately.
- **Concurrent reloads.** If a topology change triggers `LOAD_SHARD` while a
  prior `LOAD_SHARD` is still in flight, the worker's `_shard_lock` already
  serializes. Confirm under fuzz.

## Test plan

- **Unit**: tensor name selection for a range `[a, b)` matches expectation
  for Qwen, Llama configs (use real safetensors index files, mocked open).
- **Unit**: quantized tensor groups loaded together (no orphan `scales`).
- **Integration / real model**: load Qwen2.5-0.5B-4bit with layer range
  `[0, 12)`, generate one token, compare logits to full-model reference
  (within numerical tolerance).
- **Peak RAM test**: in a subprocess, load a real model with half the layers,
  measure `ru_maxrss`, assert <60% of full model size.
- **Re-plan test**: load `[12, 25)`, then `[8, 30)`, then `[0, 64)`. Final
  state should produce same outputs as a fresh full load.

## Out of scope

- Streaming download (we keep the model fully on disk).
- Cross-architecture: only modern decoder-only transformers.
- Hub also being a worker (separate plan if needed).
- Eliminating the full-disk requirement (we'd need partial download, big
  follow-up if memory savings on disk become a constraint).

## Followups

- Once stable in dots, propose upstream into mlx-lm as a public
  `load_partial(model_id, layer_start, layer_end)` API.
- [[mlx-decoupled-hub]] also benefits — both touch the same code path.
- Sets up the "small per-worker resident set" precondition for
  microbatching ([[microbatching]]) to actually matter on big pools.

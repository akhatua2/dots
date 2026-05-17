# dots

Distributed LLM inference across laptops on a LAN.

This is a small Python PoC: a transformer model is split layer-wise across two
or more Macs on the same Wi-Fi. One Mac runs the coordinator (and also owns
part of the model). The others run worker daemons (`dots serve`) and own the
rest. Inference is pipeline-parallel: each generated token traverses all
nodes once.

## What's here

```
dots/
├── protocol.py     length-prefixed msgpack frames over asyncio TCP
├── shard.py        loads an mlx-lm model, runs only an assigned layer range
├── worker.py       TCP server that hosts one shard
├── coordinator.py  connects to workers, plans the split, runs the chat loop
├── ui.py           tiny rich-based terminal styling
└── cli.py          argparse entry point: `dots serve` and `dots chat`
```

## Quick start (coordinator side — this Mac)

```bash
cd ~/Desktop/dots
uv venv
uv pip install -e .

# Interactive chat against your partner's worker at e.g. 10.30.1.42:5555
.venv/bin/dots chat --workers 10.30.1.42:5555

# One-shot, non-interactive:
.venv/bin/dots chat --workers 10.30.1.42:5555 --prompt "hi" --max-tokens 50

# Solo (no remote workers) — runs everything locally:
.venv/bin/dots chat
```

## Worker side (partner's Mac)

Get the code onto their machine (either `scp -r ~/Desktop/dots them@their-ip:Desktop/`
or push this folder to a git repo and `git clone`), then on their Mac:

```bash
cd ~/Desktop/dots
uv venv
uv pip install -e .
.venv/bin/dots serve --port 5555
```

The worker prints its listening address and waits. Tell the coordinator that
IP (find with `ipconfig getifaddr en0` on macOS Wi-Fi).

## How a request flows

```
you type a prompt
   │
   ▼  tokenize + embed
[local layers 0…k]
   │  serialize hidden state -> TCP -> deserialize
   ▼
[partner layers k…N]
   │  serialize hidden state -> TCP -> deserialize
   ▼
local norm + lm_head + sample next token
   │
   └── feed back as input to next forward (KV cache persists in each shard)
```

Hidden state per token is `[1, 1, hidden_size]` in bf16 → wire-encoded as fp16.
For SmolLM2-135M (`hidden_size=576`), that's ~1.2 KB per hop, per token.
Prefill sends the full prompt sequence in one shot.

## Model

The default is `mlx-community/SmolLM2-135M-Instruct` (~270 MB, 30 layers).
Override with `--model`:

```bash
.venv/bin/dots chat --workers 10.30.1.42:5555 \
    --model mlx-community/Qwen2.5-0.5B-Instruct-4bit
```

Both nodes must use the same `--model`. mlx-lm auto-downloads on first run.

## Design notes

- **Same model loaded on every node.** Each node only *executes* its assigned
  layer range, but the full weights are resident. This keeps the PoC simple;
  a future version can shard weights at load time.
- **KV cache lives per node.** Each shard maintains the KV cache for its
  layer range only. `RESET_KV` clears all caches between requests.
- **Greedy sampling.** No temperature, no top-p — kept minimal. Sampling
  happens on the coordinator (it owns the final norm + lm_head).
- **Capacity-aware split.** When workers handshake, they report free RAM,
  and the coordinator splits layers proportionally.
- **No auth, no encryption.** Trusted LAN only. Don't expose `dots serve`
  to the open internet.

## Known limitations (PoC)

- Single coordinator → multiple workers, no peer-to-peer between workers.
- No retry / reconnect if a worker drops mid-generation.
- No batching — strictly batch=1, single user.
- Both nodes redundantly load the full model into memory.
- No streaming output to the network; tokens are decoded on the coordinator
  after `lm_head`. (Streaming is the next thing to add.)

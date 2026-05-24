# Troubleshooting

Find the heading that matches your problem. All commands run from `core/`.

---

## Install issues

### `command not found: git`

macOS hasn't installed developer tools. Run:

```bash
xcode-select --install
```

A popup appears — click **Install** and wait 2–5 minutes. Then re-run
the install commands.

### Bootstrap script fails on `uv`

The script downloads `uv` (a Python tool) from the internet. If your
network blocks the install URL, try installing it directly:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then re-run `./core/scripts/bootstrap.sh`.

### `./core/scripts/bootstrap.sh: Permission denied`

Mark it executable:

```bash
chmod +x ./core/scripts/bootstrap.sh
./core/scripts/bootstrap.sh
```

---

## Connection issues

dots connects over [iroh](https://iroh.computer) (QUIC p2p with NAT traversal
and relay fallback), so there's no Wi-Fi, firewall, or port setup to get
wrong — but a few things can still block a connection.

### Chat/worker can't reach the hub

- **Wrong or missing join token.** Connecting requires the pool's
  `DOTS_JOIN_TOKEN`. Confirm you exported it:
  `DOTS_JOIN_TOKEN=your_token uv run dots chat`.
- **Stale saved hub.** dots remembers the last hub in `~/.dots/last_hub`. If
  you switched pools, pass the ticket explicitly, or wipe it:
  `rm ~/.dots/last_hub`.
- **No internet.** iroh needs outbound internet to reach relays for the
  initial handshake. Confirm the Mac is online.

### The connection feels slow

iroh tries a direct hole-punched path but falls back to a relay when it can't
punch through (some carrier-grade / mobile NATs). Relayed traffic adds latency.
It still works, just slower — try a wired/home network if you can.

### Speed is much slower than expected

Two common causes, in rough order:

1. **Long distance.** Cross-continent adds ~150 ms per word generated, so
   5–6 words/sec is roughly the ceiling when a worker is on another continent.
2. **Bigger model.** Try the default
   `mlx-community/Qwen2.5-0.5B-Instruct-4bit` first to isolate.

---

## Hub and worker issues

### Chat says "pool not ready" or hangs

The hub is up but no workers have finished loading yet. Either:

- **No workers running.** Start at least one with
  `DOTS_JOIN_TOKEN=your_token uv run dots worker`. Or just run `uv run dots chat`
  — it auto-spawns a companion worker.
- **Workers still downloading.** First run = full model download per worker
  (2–5 minutes). Watch each worker's Terminal for progress.

### "pool capacity X layers < Y required"

The pool can't hold the whole model. Each worker reports a layer capacity
(from its free RAM and any `--max-ram` cap), and the sum is less than the model
needs. Options:

- Add more workers.
- Switch to a smaller model with `--model mlx-community/Qwen2.5-0.5B-Instruct-4bit`.
- If a worker has `--max-ram` set artificially low, raise or remove it.

### Worker exits with "no shard loaded"

The hub re-planned mid-generation and the worker's shard was dropped. Usually
resolves itself in 10–30 seconds; if it persists, restart the worker.

### Chat output is gibberish or repeats the same word

Usually a model mismatch. Confirm every worker is running the same model as the
hub. Restart all workers — they re-fetch the correct model identity from the
hub on connection.

---

## Resetting state

### "I just want to start over"

Wipe everything dots stored locally:

```bash
rm -rf ~/.dots/
```

This deletes your worker's UUID, the last-used hub, and any cached credits.
Downloaded model weights live in `~/.cache/huggingface/` — leave those alone
unless you want to re-download.

### A `dots worker` PID is stuck and won't restart

Check `~/.dots/worker.pid`; if that PID isn't actually running
(`ps -p <pid>` returns nothing), delete the file:

```bash
rm ~/.dots/worker.pid
```

### Model download is stuck

Press `Ctrl + C` and try again. To see download progress, run the hub with
`uv run dots hub --verbose`. The model lives in `~/.cache/huggingface/`; delete
that folder to force a full re-download.

---

## Still stuck?

Open a GitHub issue at <https://github.com/akhatua2/dots/issues/new>.
Include: which step failed, the full Terminal output, and which Macs are in
your pool (chip, RAM, macOS version).

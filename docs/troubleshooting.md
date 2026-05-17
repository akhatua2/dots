# Troubleshooting

Find the heading that matches your problem.

---

## Install issues

### `command not found: git`

macOS hasn't installed developer tools. Run:

```bash
xcode-select --install
```

A popup appears — click **Install** and wait 2–5 minutes. Then re-run
the install commands.

### `command not found: brew`

Install Homebrew from [brew.sh](https://brew.sh) — paste the command
at the top of that page into Terminal. Or, for Tailscale specifically,
download it from [tailscale.com/download](https://tailscale.com/download)
directly.

### Bootstrap script fails on `uv`

The script downloads `uv` (a Python tool) from the internet. If your
network blocks the install URL, try installing it directly:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then re-run `./scripts/bootstrap.sh`.

### `./scripts/bootstrap.sh: Permission denied`

Mark it executable:

```bash
chmod +x ./scripts/bootstrap.sh
./scripts/bootstrap.sh
```

---

## Networking issues

### Worker says "Connection refused" or "No route to host"

Almost always a network problem:

- **Same Wi-Fi?** Confirm both Macs are on the **exact same Wi-Fi
  network name**. Guest networks often block device-to-device traffic.
  Switch to your home network or use Tailscale.
- **Tailscale?** On both Macs, click the menu bar icon and confirm
  it says "Connected." Run `tailscale status` to see all peers.
- **Firewall?** On the **hub** Mac, go to **System Settings → Network
  → Firewall → Options**. Make sure Python is set to "Allow incoming
  connections."

### Tailscale issues

If `tailscale ping HUB_ADDRESS` doesn't get any replies:

- Make sure both Macs are signed into the **same tailnet** (check the
  email address shown in the Tailscale menu).
- Run `tailscale status` on both Macs. Each Mac should show up in the
  other's list.
- Friend hasn't accepted the invite yet? Resend it from
  [login.tailscale.com](https://login.tailscale.com) → **Users**.

If `tailscale ping` says **`via DERP`**:

Your traffic is being relayed through Tailscale's servers instead of
going directly Mac-to-Mac. This adds 50–100 ms per hop. Restart
Tailscale on both Macs (click the icon → **Quit**, then reopen). Most
networks negotiate direct after a few seconds.

Some carrier-grade NAT (mostly mobile networks) can't escape DERP at
all. Tailscale still works, just slower.

### Speed is much slower than expected

Three causes, in rough order:

1. **Long round-trip on Tailscale.** Cross-continent is ~150 ms RTT.
   Each token does at least one full pipeline hop, so 5–6 tok/s is
   the ceiling cross-continent.
2. **DERP relay** instead of direct. See above.
3. **Bigger model.** Try the default
   `mlx-community/Qwen2.5-0.5B-Instruct-4bit` first to isolate.

Run `tailscale ping HUB_ADDRESS` to see the actual round-trip time
and whether you're direct or via DERP.

---

## Hub and worker issues

### Chat says "pool not ready" or hangs

The hub is up but no workers have finished loading yet. Either:

- **No workers running.** Start at least one with
  `.venv/bin/dots worker HUB:5555`. Or run `dots chat` on the hub Mac
  itself — it auto-spawns one.
- **Workers still downloading.** First run = full model download per
  worker (2–5 minutes). Look at each worker's Terminal to see
  progress.

### "pool capacity X GB < Y GB required"

The model is bigger than the combined RAM of all your workers.
Options:

- Add more workers.
- Switch to a smaller model with `--model
  mlx-community/Qwen2.5-0.5B-Instruct-4bit`.
- If a worker has `--max-ram` set artificially low, raise it.

### Hub Mac sleeps and everyone disconnects

The hub closed its lid or hit a sleep timer. On the hub Mac:

```bash
sudo pmset -a sleep 0
```

(Asks for your Mac password. Sets sleep timer to "never." Restore
later with `sudo pmset -a sleep 10`.)

Better: keep the hub Mac plugged in, lid open, display on.

### Worker exits with "no shard loaded"

The hub re-planned mid-generation and the worker's shard was dropped.
This usually resolves on its own in 10–30 seconds. If it persists,
restart the worker.

### Chat output is gibberish or repeats the same word

Usually a model mismatch. Confirm every worker is running the same
model as the hub. Restart all workers — they'll re-fetch the correct
model identity from the hub on connection.

---

## Resetting state

### "I just want to start over"

Wipe everything dots stored locally:

```bash
rm -rf ~/.dots/
```

This deletes your worker's UUID, the last-used hub address, and any
cached credits. Downloaded model weights live in
`~/.cache/huggingface/` — leave those alone unless you want to
re-download.

### A `dots worker` PID is stuck and won't restart

Check `~/.dots/worker.pid`:

```bash
cat ~/.dots/worker.pid
```

If that PID isn't actually running (`ps -p <pid>` returns nothing),
delete the file:

```bash
rm ~/.dots/worker.pid
```

### Model download is stuck

Press `Ctrl + C` and try again. To see download progress:

```bash
.venv/bin/dots hub --verbose
```

The model lives in `~/.cache/huggingface/`. Delete that folder to
force a full re-download.

---

## Still stuck?

Open a GitHub issue at <https://github.com/akhatua2/dots/issues/new>.
Include: which step failed, the full Terminal output, and which Macs
are in your pool (chip, RAM, macOS version).

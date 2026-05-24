# Setting up dots

Run a model across several Apple Silicon Macs. There's no networking to
configure — dots connects to the hub over [iroh](https://iroh.computer)
(QUIC peer-to-peer with NAT traversal), so no Tailscale, no port forwarding,
no firewall rules.

If anything goes wrong, see [troubleshooting.md](./troubleshooting.md).

---

## The no-terminal path

If you just want to **contribute compute**, you don't need any of this. Open
the web app with your invite link, click **Contribute**, and double-click the
file it downloads. See the [root README](../README.md).

The rest of this guide is the terminal path, for developers and for running
your own hub.

---

## What you'll need

- An **Apple Silicon Mac** (M1 or later) with **8 GB+ RAM**, macOS 13+.
- Your pool's **join token** (the `DOTS_JOIN_TOKEN` from your invite).

You do **not** need a GPU, a cloud account, or any paid API key.

---

## Step 1 — Install

```bash
cd ~/Desktop
git clone https://github.com/akhatua2/dots.git
cd dots
./core/scripts/bootstrap.sh
```

The script installs `uv` if needed and sets up the engine (with the Apple
mlx backend) under `core/`. If macOS prompts to install command-line
developer tools, click **Install**, wait a few minutes, then re-run it.

All `dots` commands run from `core/`:

```bash
cd core
uv run dots --help     # hub / chat / worker / serve / credits / account
```

---

## Step 2 — Chat (and contribute at the same time)

The hub address is baked in, so no address is needed. Set your join token and go:

```bash
cd core
DOTS_JOIN_TOKEN=your_token uv run dots chat
```

`dots chat` quietly starts a background worker so your session also contributes
compute. Pass `--no-worker` to chat without donating. Type a prompt, press
`Enter`, watch it stream; `Ctrl + C` to exit.

Pick a non-default model the pool can serve:

```bash
uv run dots chat --model mlx-community/Qwen3-9B-4bit
```

---

## Step 3 — Contribute compute only

To donate compute without chatting:

```bash
cd core
DOTS_JOIN_TOKEN=your_token uv run dots worker
```

Leave the window open while you want to contribute. Your Mac appears on the
pool map once it connects.

---

## Running your own hub

Any Mac (or a Linux VM) can be a hub. It coordinates sharding, token routing,
and credit accounting, and exposes an OpenAI-compatible HTTP API on port 8080.

```bash
cd core
DOTS_JOIN_TOKEN=your_token uv run dots hub --http-port 8080
```

On startup it prints a **join ticket**. Share it; others connect with:

```bash
DOTS_JOIN_TOKEN=your_token uv run dots chat <TICKET>
```

dots remembers the last hub in `~/.dots/last_hub`, so after the first connect
you can just run `uv run dots chat` with no ticket.

To deploy a permanent always-on hub on a VM, see [`../infra/README.md`](../infra/README.md).

---

## Compute units (CU)

dots tracks a closed **compute-unit** economy: **1 CU = 1 TFLOP**. Contributing
earns CU; querying spends it. Per turn, CU spent exactly matches CU credited to
the workers that participated — so a solo user in their own pool nets to zero,
and across a friend pool credits flow from heavy users to heavy contributors.

```bash
cd core
uv run dots credits            # your balance
uv run dots credits --all      # everyone's
```

CU isn't money — there's no gating today (you can query at a negative balance);
it's transparency about who contributes what.

---

## Accounts (optional)

Every contribution and query is attributed to an **account**. By default dots
generates a random UUID; you can instead pick a name + email + password so you
can recover the same `account_id` if you lose your `~/.dots/` directory.

```bash
cd core
uv run dots account create     # same name+email+password → same account_id
uv run dots account show
```

Your name and email stay on this Mac — only your `account_id` and `alias`
appear in pool logs. Run `account create` with the same inputs on each of your
Macs so their contributions aggregate to one account.

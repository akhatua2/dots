# Setting up dots

This guide walks you through getting dots running on two or more Macs.
No coding required — if you can open Terminal and copy/paste, you can
do this.

If anything goes wrong, jump to [troubleshooting.md](./troubleshooting.md).

---

## What you'll need

- **An Apple Silicon Mac per person** (M1, M2, M3, or M4). Click the
  Apple menu → **About This Mac** to check.
- **At least 8 GB of RAM** (more is better — each Mac holds a slice of
  the model proportional to its free memory).
- **macOS Ventura (13) or newer.**
- **One way to network the Macs together**:
  - Same Wi-Fi (easy, fast), or
  - **Tailscale** — a free app that lets your Macs talk to each other
    over the internet. Use this if your friends are in different
    buildings, if you're on university/office Wi-Fi, or anywhere
    devices can't see each other on the network.

You do **not** need a GPU, a cloud account, an OpenAI/Anthropic key,
or any paid service.

---

## Step 0 — Open Terminal

Every command in this guide goes into Terminal.

1. Press `⌘ Space` to open Spotlight.
2. Type `Terminal` and press `Enter`.
3. A window opens. Click into it, paste a command with `⌘ V`, hit
   `Enter`.

That's the only skill you need.

---

## Step 1 — Install dots on every Mac

Do this once on **every Mac** that will be part of your pool.

### 1a. Download dots

```bash
cd ~/Desktop
git clone https://github.com/akhatua2/dots.git
cd dots
```

If macOS pops up asking to install **command-line developer tools**,
click **Install**, wait 2–5 minutes, then re-run the three commands.

### 1b. Run the setup script

```bash
./scripts/bootstrap.sh
```

Takes 2–3 minutes. When it finishes you'll see:

```
dots installed.
```

### 1c. Confirm it works

```bash
.venv/bin/dots --help
```

You should see a help message listing `hub`, `chat`, `worker`, `account`,
and `credits`. You're done with installation.

### 1d. (Optional but recommended) Set up your account

Every contribution and every query gets attributed to an **account** — your
identity in the pool. By default dots creates a random UUID for you, but
you can also pick a name + email + password so you can recover the same
account_id if you ever lose your `~/.dots/` directory.

```bash
.venv/bin/dots account create
# Prompts for name, email, alias, and an optional password.
# Same name + email + password always produces the same account_id.
```

After setup, `dots account show` displays what you've stored. Your
**name** and **email** stay on this Mac only — they're never sent over the
network. Only your **account_id** and **alias** appear in pool logs.

> **Tip:** If you have multiple Macs, run the same `dots account create`
> with the same inputs on each of them. They'll all credit one account,
> so your contributions aggregate.

---

## Step 2 — Pick the hub Mac

One Mac in your group plays the **hub** role — it coordinates the
others. Any Mac can be the hub; pick whichever:

- Stays plugged in and open most of the time.
- Has the best internet connection.

The hub also contributes compute, so it's not "doing extra work" — it
just has the coordinator role.

The hub Mac is the one whose address everyone else types in. Knowing
*which* Mac is the hub matters more than anything else in this guide.

---

## Step 3 — Connect the Macs

Pick **one** of the two options below.

### Option A — Same Wi-Fi

Use this if every Mac is on the **exact same Wi-Fi network name**.

**Heads up:** most university, hotel, and corporate Wi-Fi networks
block device-to-device traffic. If `ping` fails between your Macs,
switch to Tailscale (Option B).

On the **hub** Mac:

```bash
ipconfig getifaddr en0
```

You'll see something like `192.168.1.42`. That's your **hub address**.
Write it down.

If `en0` prints nothing, try `ipconfig getifaddr en1`.

To check the other Mac can reach the hub, run this on a friend's Mac:

```bash
ping 192.168.1.42
```

You should see replies. Press `Ctrl + C` to stop. If you see
"Request timeout" or "No route to host", switch to Tailscale.

Skip to Step 4.

### Option B — Tailscale

Tailscale is a free app that builds a private network between your
Macs no matter where they are. It works through firewalls and routers
with no configuration. Free plan covers 100 devices and 3 users.

See [tailscale.md](./tailscale.md) for the full Tailscale walkthrough.

When you finish that guide you'll have a hub address like
`100.64.12.34`. Come back here for Step 4.

---

## Step 4 — Start the hub

On the **hub** Mac:

```bash
cd ~/Desktop/dots
.venv/bin/dots hub
```

After 10–30 seconds (loading the model into memory), you'll see
something like:

```
loading model mlx-community/Qwen2.5-0.5B-Instruct-4bit on hub…
model ready (24 layers)
hub listening on ('0.0.0.0', 5555) — waiting for workers to join
```

**Important:** leave this Terminal window open. Closing it shuts down
the hub. You can minimize it.

---

## Step 5 — Each friend joins as a worker

On every Mac **other than the hub**:

```bash
cd ~/Desktop/dots
.venv/bin/dots worker HUB_ADDRESS:5555
```

Replace `HUB_ADDRESS` with the address from Step 3:

```bash
# Same Wi-Fi:
.venv/bin/dots worker 192.168.1.42:5555

# Tailscale:
.venv/bin/dots worker 100.64.12.34:5555
```

**macOS firewall popup.** The first time you run a worker, macOS
asks if Python can accept incoming network connections. Click
**Allow** — the worker can't be reached otherwise.

If you missed it: **System Settings → Network → Firewall → Options**
→ find the Python entry → set it to "Allow incoming connections."

The worker connects, downloads the model on first run (2–5 minutes),
and announces its layer range:

```
···  dots  worker abc12345 · layers 12:24
ctrl-c to disconnect
```

Leave this Terminal open while you want to contribute compute.
Repeat on every additional Mac.

---

## Step 6 — Chat

From any Mac that can reach the hub:

```bash
cd ~/Desktop/dots
.venv/bin/dots chat HUB_ADDRESS:5555
```

Type a prompt, press `Enter`, watch the answer stream back. Each turn
shows speed in tokens/second.

Press `Ctrl + C` to exit chat.

> **Shortcut.** dots remembers the last hub address in
> `~/.dots/last_hub`. Next time you can just run
> `.venv/bin/dots chat` with no address.

> **Bonus.** Running `dots chat` quietly starts a background worker
> on the same Mac so your session also contributes compute. Pass
> `--no-worker` to disable.

---

## You're done

To summarize what's running:

| Mac | Command | Stop with |
|---|---|---|
| Hub | `.venv/bin/dots hub` | `Ctrl + C` |
| Friend Mac(s) | `.venv/bin/dots worker HUB:5555` | `Ctrl + C` |
| Anyone chatting | `.venv/bin/dots chat HUB:5555` | `Ctrl + C` |

To restart everything tomorrow: open Terminal on each Mac and re-run
the same commands. Nothing needs to be reinstalled.

---

## Step 7 — (Optional) Check your CU balance

dots tracks a **closed compute-unit (CU) economy**. **1 CU = 1 TFLOP** of
work (one trillion floating-point operations). Contributing compute earns
CU; querying spends CU. Per-turn, every CU spent matches exactly what's
credited to the workers that participated — so a solo user in their own
pool always nets to zero, and across a friend pool credits flow naturally
from heavy users to heavy contributors.

The hub exposes an HTTP API on port `8080` by default (pass `--http-port 0`
to disable). Check balances from any Mac:

```bash
.venv/bin/dots credits HUB_ADDRESS:8080            # your balance
.venv/bin/dots credits HUB_ADDRESS:8080 --all      # everyone's
```

CU isn't real money — it's an internal accounting layer. There's no
gating today (you can query even with a negative balance), it's purely
transparency about who's contributing what.

---

## Common problems

See [troubleshooting.md](./troubleshooting.md).

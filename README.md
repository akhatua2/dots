# dots

**Run a large language model across the laptops you already own.**

`dots` splits one model in half (or in thirds, or across ten devices) and runs
each part on a different Mac. You and your friends can pool your laptops
together over Wi-Fi or over the internet (via Tailscale) and chat with a model
that's collectively too big for any single machine — or just too slow on its
own.

It looks like this:

```
  ╭──────────────────────────────────────────────────────────────╮
  │   ···  dots                                                  │
  │                                                              │
  │   model:  Qwen2.5-0.5B-Instruct-4bit                         │
  │   pool:   you (layers 0-12) ◀──▶ friend (layers 12-24)       │
  │                                                              │
  │   › what's the capital of france?                            │
  │     The capital of France is Paris.                          │
  │                                                              │
  │   38 tok/s · 12 prompt · 8 response                          │
  ╰──────────────────────────────────────────────────────────────╯
```

---

## Who can run this

You need:

- **An Apple Silicon Mac** (M1, M2, M3, M4, etc.) per person. dots uses
  Apple's MLX framework, so Intel Macs and other operating systems won't work yet.
- At least **8 GB of RAM** per Mac (more is better — your Mac will hold a
  slice of the model proportional to its free memory).
- A way to network the Macs together. Two options:
  - Same Wi-Fi network — easy, fast.
  - **Tailscale** (free, works across the internet) — slower, but lets you pool
    Macs that aren't in the same building. Recommended for most setups.

You do **not** need a GPU server, a cloud VM, an OpenAI key, or any
paid service.

---

## Install (one-time, on each Mac)

> **First time setting this up?** Read [`docs/setup.md`](./docs/setup.md).
> It walks through every step in plain language. For internet-pooling
> setup see [`docs/tailscale.md`](./docs/tailscale.md). Stuck? See
> [`docs/troubleshooting.md`](./docs/troubleshooting.md).
> The summary below assumes you've opened a Terminal before.

Open the **Terminal** app (press `⌘ Space`, type `Terminal`, hit enter).

Copy and paste these commands, one at a time. Press `Enter` after each.

```bash
# 1. Download dots into your Desktop folder
cd ~/Desktop
git clone https://github.com/akhatua2/dots.git
cd dots

# 2. Run the setup script (installs Python tooling + dependencies)
./scripts/bootstrap.sh
```

The setup takes 2-3 minutes. When it finishes, you'll see `dots installed.`

You only need to do this once per Mac.

> **No `git` installed?** macOS asks to install command-line tools the first
> time you run `git`. Click **Install** in the popup, wait a couple of minutes,
> then re-run the commands above.

---

## Pick one Mac to be the "hub"

One Mac in your group plays the role of **hub** — it coordinates the others.
Any Mac can be the hub; pick whichever stays awake and online most reliably.
The hub also contributes compute, so it's not "wasted."

Everyone else (including the hub's owner, if they want to chat) connects to
the hub from their own Mac.

---

## Step 1 — Connect the Macs to each other

### Option A: Same Wi-Fi network (easiest)

On the **hub** Mac, find its Wi-Fi address:

```bash
ipconfig getifaddr en0
```

It will print something like `192.168.1.42`. That's the hub's address on your
network. Write it down — you'll give it to the others.

> If `en0` prints nothing, try `ipconfig getifaddr en1` (some Macs use a
> different interface).

Skip ahead to **Step 2**.

### Option B: Tailscale (works across the internet)

If your friends aren't in the same building, or your office Wi-Fi blocks
device-to-device connections (a lot of campus and corporate networks do),
use Tailscale. It's free, takes 5 minutes, and works across continents.

**On every Mac:**

1. Install Tailscale: `brew install --cask tailscale` (or download it from
   [tailscale.com/download](https://tailscale.com/download)).
2. Open the Tailscale app (it'll appear in your menu bar — top-right of the
   screen).
3. Click the icon → **Log in** → sign in with Google/GitHub/email.

**Invite your friends to the same tailnet:**

The hub's owner goes to [login.tailscale.com](https://login.tailscale.com),
clicks **Users → Invite users**, enters each friend's email, and Tailscale
emails them a magic link. Each friend opens the email on their Mac and clicks
the link — that joins them to the same tailnet.

(Tailscale's free plan covers 100 devices and 3 users, which is plenty.)

**Find the hub's tailnet address:**

On the **hub** Mac, run:

```bash
tailscale ip -4
```

It prints something like `100.64.12.34`. That's the address every Mac in your
group will use to reach the hub.

**Verify it works:**

On a different Mac in the group, run:

```bash
tailscale ping 100.64.12.34
```

(Replace the address with the one from above.) You should see "pong" replies.
If you don't, double-check both Macs are signed in to Tailscale and on the
same tailnet.

---

## Step 2 — Start the hub

On the **hub** Mac, in Terminal:

```bash
cd ~/Desktop/dots
.venv/bin/dots hub
```

The hub starts up and waits. You'll see something like:

```
hub listening on 0.0.0.0:5555
loading model mlx-community/Qwen2.5-0.5B-Instruct-4bit…
ready (24 layers)
```

**Leave this Terminal window open.** Closing it shuts down the hub.

If you want the OpenAI-compatible HTTP API too (so you can point apps like
Continue, Cursor, LangChain, or plain `curl` at dots):

```bash
.venv/bin/dots hub --http-port 8080
```

---

## Step 3 — Each friend joins as a worker

Each other Mac runs `dots worker` and points at the hub's address.

**Open Terminal on the friend's Mac:**

```bash
cd ~/Desktop/dots
.venv/bin/dots worker HUB_ADDRESS:5555
```

Replace `HUB_ADDRESS` with the address from Step 1 (either the Wi-Fi IP
like `192.168.1.42` or the Tailscale IP like `100.64.12.34`).

Examples:

```bash
.venv/bin/dots worker 192.168.1.42:5555      # same Wi-Fi
.venv/bin/dots worker 100.64.12.34:5555      # over Tailscale
```

You'll see the worker connect, download the model (first time only — takes a
minute), and report its assigned layer range. **Leave this Terminal open**
while you want to contribute compute.

> macOS will pop up a **firewall dialog** asking if Python can accept incoming
> connections. Click **Allow** — the worker can't be reached otherwise. If you
> missed it: System Settings → Network → Firewall → Options → flip the Python
> entry to "Allow incoming connections."

You can keep adding more workers — every Mac that runs `dots worker` joins the
pool and takes over some of the layers, lightening the load on everyone else.

---

## Step 4 — Chat

From any Mac (the hub itself, or any of the workers, or a fourth one that's
not contributing compute), open the chat:

```bash
cd ~/Desktop/dots
.venv/bin/dots chat HUB_ADDRESS:5555
```

You'll see a Claude-Code-style chat box. Type a prompt, press Enter, and watch
the answer stream back. Below the message you'll see how fast it ran:

```
38 tok/s · 12 prompt · 84 response
```

Press `Ctrl+C` to exit.

> **Shortcut:** the first time you connect, dots remembers the hub's address in
> `~/.dots/last_hub`. After that, you can just run `dots chat` with no
> arguments.

> **Auto-donation:** when you run `dots chat` and you're not already running a
> worker on that Mac, dots spawns a worker in the background so your machine
> chips in compute too. It shuts down when you exit chat. Disable with
> `--no-worker` if you want.

---

## How fast will it be?

Speed depends on two things: **how powerful each Mac is**, and **how fast the
network between them is**.

| Setup | Typical speed |
|-------|---------------|
| Two M2/M3 Macs on the same Wi-Fi | 30-60 tokens/second |
| Two Macs across a city on Tailscale | 10-20 tokens/second |
| Two Macs across continents on Tailscale | 3-6 tokens/second |
| One Mac, no pool (solo) | depends on the model size |

A token is roughly three-quarters of an English word. "30 tok/s" means about
22 words per second, which is faster than most people read.

---

## What model is it running?

By default: `mlx-community/Qwen2.5-0.5B-Instruct-4bit` — a small,
fast 0.5-billion-parameter model. It's not GPT-4. It's good for chat, simple
Q&A, summaries, and feeling out the system.

To run a different model, start the hub with `--model`:

```bash
.venv/bin/dots hub --model mlx-community/Qwen2.5-7B-Instruct-4bit
```

Any [mlx-community model](https://huggingface.co/mlx-community) works. The
hub downloads it on first run (large models take a while the first time).
Bigger models need more Macs in the pool to fit — that's the whole point of
this project.

---

## Common problems

**"Permission denied" or "no such file" when running `.venv/bin/dots`**
You're not in the dots folder. Run `cd ~/Desktop/dots` first.

**Worker can't reach the hub ("no route to host" / "connection refused")**
Most likely your network blocks device-to-device traffic — common on
university Wi-Fi, hotels, and corporate networks. Use **Tailscale** (Option B
in Step 1).

**Chat says "pool not ready"**
The hub is up but no workers have joined yet. Start at least one worker (or
run `dots chat` on the hub Mac itself — it auto-spawns one).

**Hub shuts down when I close my laptop**
Yes — closing the lid suspends the Mac. Keep the hub Mac plugged in, open, and
awake. (Coming soon: a `LaunchAgent` so the hub auto-starts at login.)

**Model download is stuck**
The first download can be a few hundred MB. Check your internet, and try again.
If it keeps failing, run `.venv/bin/dots hub --verbose` to see what's happening.

---

## What's actually happening under the hood

If you're curious: the model is a stack of "layers" (24 of them for the
default model). Each Mac in the pool is assigned a *slice* of those layers
based on how much free RAM it has. When you send a prompt, it flows through
the pool like a relay race: each Mac runs its slice, then ships the
intermediate result over the network to the next Mac, until the last one
spits out the next word. Then back around for the word after that.

```
your prompt
   │
   ▼ tokenize + embed
[your Mac · layers 0-12]
   │
   ▼ ship hidden state over TCP
[friend's Mac · layers 12-24]
   │
   ▼ final norm + sample next word
your screen ◀──────────────┘
   │
   └── feed back, generate the next word
```

For nerds: bf16 hidden states are bit-cast to uint16 for lossless wire
transport. KV caches live per-conversation on each worker. The hub recovers
from mid-generation worker drops by re-prefilling on the remaining workers.

The full code is in [`dots/`](./dots/) — `hub.py`, `worker.py`, `chat.py`,
`shard.py`, `protocol.py`, `peer.py`, `api.py`, `cli.py`. ~2k lines, Python
only.

---

## Trust and security

dots assumes the Macs in your pool **trust each other**. There's no end-to-end
encryption between hub and workers (Tailscale handles that for you when you
use it — WireGuard under the hood). Workers can see the intermediate hidden
states of prompts they process, which means a malicious worker could in
principle try to reconstruct what's being asked.

**Use dots with people you'd lend your laptop to.** A public pool with
strangers needs the auth, allowlist, and audit features that are on the
roadmap but not built yet.

---

## License & contributing

PoC stage. Open an issue or PR if something doesn't work or you'd like to add
something. The code aims to be readable end-to-end.

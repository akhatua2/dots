# Cloud hub deploy

| | |
|---|---|
| **Status** | proposed |
| **Effort** | XS |
| **Complexity** | low |
| **Risk** | low |
| **Depends on** | [[mlx-decoupled-hub]] |
| **Unlocks** | always-on hub at $0/mo; stable address for friend pool |

## Why

Once the hub doesn't need MLX, we can run it on Linux for free. Oracle Cloud
Free Tier offers 4 ARM cores + 24 GB RAM + 200 GB SSD, **free forever, no
time limit**. That's a real always-on hub for $0/month, no credit card
recurring charge.

Today the hub runs on the user's laptop. If the laptop closes or sleeps,
the pool dies. A cloud hub is always reachable; workers (and chat clients)
can come and go.

## What changes

This is ops + docs, mostly. Code changes are limited to:

- `scripts/cloud-deploy.sh` — provision script for an Oracle Cloud ARM VM:
  install Python, clone dots, install systemd unit, attach Tailscale.
- `scripts/dots-hub.service` — systemd unit for always-on with auto-restart.
- `scripts/cloud-update.sh` — pulls latest dots and restarts the service.
  Called by a cron or a webhook.
- `docs/cloud-deploy.md` — step-by-step user guide.

No changes to `dots/` source.

## Approach

### Provider choice

| Provider | Free tier | Cost after free | Hardware |
|---|---|---|---|
| **Oracle Cloud Free** | 4 cores Ampere ARM, 24 GB RAM, 200 GB SSD, **forever** | $0 | ARM Linux |
| Hetzner Cloud CAX11 | none | €3.79/mo | ARM Linux |
| Google Cloud e2-micro | 1 GB RAM | $0 | x86 Linux |
| AWS t2.micro | 12 months only | $8/mo after | x86 Linux |
| Fly.io | 256 MB shared | $1.94/mo | mixed |

**Oracle Cloud Free** is the right answer: most resource, free forever,
ARM is fine (numpy works), comparable to a small Mac. Accept that the
sign-up flow is annoying.

### Hub access

Two ways friends connect to the cloud hub:

| Method | How | Trade-off |
|---|---|---|
| **Tailscale** | Hub joins your tailnet; friends connect to its tailnet IP | private, friend setup unchanged from today |
| **Tailscale Funnel** | Hub exposes port 5555 publicly via funnel | public address, no install required for friends, slower |
| **Cloudflare Tunnel** | Hub registers a Cloudflare tunnel | public, custom domain |

For v1: **Tailscale** (already used today). Funnel is a one-liner upgrade
later (`sudo tailscale funnel 5555 on`) if you want friends without
Tailscale accounts.

### Auto-update on `git push`

Three tiers from cheap to fancy:

| Tier | How | Latency | Effort |
|---|---|---|---|
| **Cron poll** | `* * * * * pull && restart-if-changed` | up to 1 min | XS |
| **GitHub webhook** | tiny aiohttp listener on hub, GitHub posts on push | <5 s | S |
| **GitHub Actions self-hosted runner** | proper CI/CD | <30 s | M |

Ship tier 1 by default. The cron poll is 3 lines. Upgrade later if 1-minute
latency feels slow.

### Provisioning script outline

```
1. ssh to fresh Oracle VM
2. apt install python3.11-venv git curl tailscale
3. git clone https://github.com/akhatua2/dots ~/dots
4. cd ~/dots && ./scripts/bootstrap.sh
5. echo HF_TOKEN=... >> ~/.dots/env
6. install systemd unit, enable
7. install cron poll, enable
8. tailscale up
9. print the tailnet IP to give to friends
```

Total time: ~5 minutes including the Oracle sign-up.

## Key design decisions

| Option | Decision |
|---|---|
| **Free tier (Oracle)** | **chosen** |
| **Cheap VPS (Hetzner)** | acceptable backup at ~$4/mo if Oracle proves flaky |
| **Self-hosted Mac mini** | parallel option (see [[mac-launchagent-hub]]) |

| Option | Decision |
|---|---|
| **systemd** | **chosen** — standard on Linux |
| **bare nohup / tmux** | rejected — no auto-restart |
| **Docker** | considered — overkill for one process |

## Open questions

- **Disk for models.** 200 GB of free SSD is enough for ~10 different 7B
  models, ~5 different 70B models. Document the practical cap.
- **Bandwidth.** Oracle Free includes 10 TB egress/month. A 7B HuggingFace
  download is 4 GB; well within budget unless we re-download often. Reuse
  HF cache.
- **HF rate limiting.** Set `HF_TOKEN` in the unit's environment so the
  VM gets authenticated rate limits.
- **Region selection.** Pick the Oracle region closest to most friends.
  US-West and EU-West are reasonable for most pools.

## Test plan

- Fresh VM → provisioning script → hub running → tailnet-reachable. Time
  this; document the result.
- `git push` from laptop → within 60 seconds, cloud hub is running the
  new code. Verify by inspecting `dots --version` (or a known string in
  the log).
- Power-cycle the VM. systemd restarts the hub on boot.
- Kill the hub process. systemd restarts it.

## Out of scope

- HTTPS termination for the OpenAI-compatible API (Tailscale Funnel
  provides https automatically; for non-Funnel deployments, add nginx
  later).
- Custom domain (use Tailscale's `*.ts.net` for now).
- Multi-region failover.

## Followups

- `dots cloud setup` CLI subcommand that automates the provisioning
  (one command, all the above).
- DNS for the hub address so friends don't have to remember a tailnet IP.

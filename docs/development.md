# Development

## Repository layout

This is a monorepo. Each top-level dir has its own README; the high level:

```
apps/              end-user clients
  web/             Vite + React web app (chat + pool map + contribute) → Vercel
  macos/           native macOS app (early stub)
core/              the Python engine — everything dots actually runs
  pyproject.toml   package + dep manifest (run uv from here)
  uv.lock
  src/dots/        hub, worker, chat, CLI, HTTP API (src layout)
    distributed/   hub.py, worker.py (iroh QUIC transport)
    app/           api.py (aiohttp HTTP server)
    system/        util, geo, accounts
    cli.py         entry point: dots hub / worker / chat / credits
  tests/           pytest suite
  packaging/       worker.spec + worker_entry.py (PyInstaller binary)
  scripts/         bootstrap.sh, sim_scheduler.py
infra/             hosted-hub deploy (systemd unit, bootstrap, README)
docs/              this folder + design notes under docs/plans/
.github/workflows/ CI/CD
```

Everything Python is rooted at `core/` — `uv` commands run from there.

## Architecture

```
[hub VM] ←─ iroh QUIC ─→ [worker Mac 1]
           ←─ iroh QUIC ─→ [worker Mac 2]
                ↕ HTTP API (ngrok static domain)
           [web app (Vercel)]
```

- **Hub** — orchestrates layer sharding, token routing, credit accounting. Runs on a Linux VM. Exposes an OpenAI-compatible HTTP API on port 8080.
- **Workers** — Apple Silicon Macs that donate compute. Connect via iroh QUIC using a join token.
- **Web app** — chat UI + pool map + contribute panel. Deployed to Vercel.

## Setup

```bash
cd core

# Install all deps (hub + worker + dev tools)
uv sync --extra mac --extra dev

# Run tests
uv run pytest

# Build the worker binary
uv run pyinstaller packaging/worker.spec --distpath dist/

# Lint
uv run ruff check src tests
```

## Running locally

**Hub:**
```bash
cd core
DOTS_JOIN_TOKEN=your_token uv run dots hub
```

**Worker (in a second terminal):**
```bash
cd core
DOTS_JOIN_TOKEN=your_token uv run dots worker
```

**Web app:**
```bash
cd apps/web
npm install && npm run dev
```

## Hub HTTP API

All endpoints except `/healthz` require `Authorization: Bearer <DOTS_JOIN_TOKEN>`.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/chat/completions` | OpenAI-compatible chat |
| GET | `/v1/models` | Available models |
| GET | `/v1/credits` | CU balances |
| GET | `/v1/pool/nodes` | Worker map data |
| GET | `/v1/download/worker` | Pre-built macOS worker binary |
| GET | `/healthz` | Liveness + worker count |

## CI/CD

| Workflow | Trigger | What it does |
|----------|---------|-------------|
| `test.yml` | PR into `main` | Runs the test suite (gate) |
| `deploy-hub.yml` | push to `main` (`core/**` or `infra/**`) | Deploys hub to GCP VM |
| `publish-binary.yml` | GitHub release published | Builds macOS arm64 binary, uploads to release assets and hub server |
| `deploy-web.yml` | push to `main` (`apps/web/**`) | Deploys web app to Vercel |

## GitHub Secrets required

| Secret | Used by | Description |
|--------|---------|-------------|
| `GCP_WIF_PROVIDER` | deploy-hub, publish-binary | GCP Workload Identity provider |
| `GCP_DEPLOY_SA` | deploy-hub, publish-binary | GCP service account email |
| `VERCEL_TOKEN` | deploy-web | Vercel API token |
| `VERCEL_ORG_ID` | deploy-web | Vercel org ID (from `vercel link`) |
| `VERCEL_PROJECT_ID` | deploy-web | Vercel project ID (from `vercel link`) |

The web app is **invite-only** — no hub URL or token is baked into the build.
Access comes from the invite-link fragment (`#t=<token>&api=<hub-url>`), saved
to `localStorage` on first open.

## Vercel setup (one-time)

```bash
cd apps/web
vercel login
vercel link          # creates .vercel/project.json
# Copy team_id → VERCEL_ORG_ID, projectId → VERCEL_PROJECT_ID into GitHub Secrets
```

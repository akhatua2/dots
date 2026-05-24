# Development

## Repository layout

```
dots/          Python package — hub, worker, CLI, API
  distributed/ hub.py, worker.py (iroh QUIC transport)
  app/         api.py (aiohttp HTTP server)
  system/      util, geo, accounts
  cli.py       entry point: dots hub / worker / chat / credits
web/           Vite + React web app
tests/         pytest suite
.github/
  workflows/   CI/CD
worker.spec    PyInstaller spec for worker binary
worker_entry.py  Entry point for PyInstaller
```

## Architecture

```
[hub VM] ←─ iroh QUIC ─→ [worker Mac 1]
           ←─ iroh QUIC ─→ [worker Mac 2]
                ↕ HTTP API (cloudflared tunnel)
           [web app (Vercel)]
```

- **Hub** — orchestrates layer sharding, token routing, credit accounting. Runs on a Linux VM. Exposes an OpenAI-compatible HTTP API on port 8080.
- **Workers** — Apple Silicon Macs that donate compute. Connect via iroh QUIC using a join token.
- **Web app** — chat UI + pool map + contribute panel. Deployed to Vercel.

## Setup

```bash
# Install all deps (hub + worker + dev tools)
uv sync --extra mac --extra dev

# Run tests
uv run pytest

# Build the worker binary
uv run pyinstaller worker.spec --distpath dist/

# Lint
uv run ruff check dots/
```

## Running locally

**Hub:**
```bash
DOTS_JOIN_TOKEN=your_token uv run dots hub
```

**Worker (in a second terminal):**
```bash
DOTS_JOIN_TOKEN=your_token uv run dots worker
```

**Web app:**
```bash
cd web
cp .env.local.example .env.local  # fill in VITE_DOTS_API + VITE_DOTS_TOKEN
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
| `test.yml` | push / PR | Runs the test suite |
| `deploy-hub.yml` | push to `main` (dots/ changed) | Deploys hub to GCP VM |
| `publish-binary.yml` | GitHub release published | Builds macOS arm64 binary, uploads to release assets and hub server |
| `deploy-web.yml` | push to `main` (web/ changed) | Deploys web app to Vercel |

## GitHub Secrets required

| Secret | Used by | Description |
|--------|---------|-------------|
| `GCP_WIF_PROVIDER` | deploy-hub, publish-binary | GCP Workload Identity provider |
| `GCP_DEPLOY_SA` | deploy-hub, publish-binary | GCP service account email |
| `VERCEL_TOKEN` | deploy-web | Vercel API token |
| `VERCEL_ORG_ID` | deploy-web | Vercel org ID (from `vercel link`) |
| `VERCEL_PROJECT_ID` | deploy-web | Vercel project ID (from `vercel link`) |
| `VITE_DOTS_API` | deploy-web | Hub URL for the web app build |
| `VITE_DOTS_TOKEN` | deploy-web | Join token for the web app build |

## Vercel setup (one-time)

```bash
cd web
vercel login
vercel link          # creates .vercel/project.json
# Copy team_id → VERCEL_ORG_ID, projectId → VERCEL_PROJECT_ID into GitHub Secrets
```

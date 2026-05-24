# dots

Distributed LLM inference — pool Apple Silicon Macs over the internet to run models too big for one machine.

A hub coordinates the pool. Workers connect from anywhere. A web app lets anyone chat with or contribute to the pool.

---

## Architecture

```
[hub VM] ←─ iroh QUIC ─→ [worker Mac 1]
           ←─ iroh QUIC ─→ [worker Mac 2]
                ↕ HTTP API
           [web app (Vercel)]
```

- **Hub** — orchestrates layer sharding, token routing, credit accounting. Runs on a Linux VM. Exposes an OpenAI-compatible HTTP API.
- **Workers** — Apple Silicon Macs that donate compute. Connect to the hub with a join token.
- **Web app** — chat UI + pool map, served from Vercel. No login — invite link carries the token.

---

## Contributing compute (workers)

Open the web app with your invite link and click **Contribute**. Two files download:

| File | Purpose |
|------|---------|
| `dots-worker` | Pre-built macOS arm64 binary |
| `hub.env` | Your join token — keep these two files together |

Double-click `dots-worker`. If macOS blocks it, right-click → **Open** once to trust it. The binary reads `hub.env` from the same folder automatically.

**Requirements:** macOS, Apple Silicon (M1+), 8 GB+ RAM.

---

## Running the hub

The hub is deployed to a Linux VM via GitHub Actions on every push to `main` that touches `dots/`.

To run locally:

```bash
uv sync
DOTS_JOIN_TOKEN=your_token uv run dots hub
```

Environment (in `~/.dots/hub.env` on the VM):

```
DOTS_JOIN_TOKEN=dots_...
```

The hub exposes:
- `POST /v1/chat/completions` — OpenAI-compatible chat
- `GET /v1/models` — available models
- `GET /v1/credits` — CU balances
- `GET /v1/pool/nodes` — worker map data
- `GET /healthz` — health + worker count

---

## Web app

The web app lives in `web/`. It's a Vite + React + Tailwind v4 SPA, deployed to Vercel.

```bash
cd web
npm install
npm run dev        # local dev
npm run build      # production build
```

Required env vars (in `web/.env.local` for dev, Vercel env for prod):

```
VITE_DOTS_API=https://your-hub-url
VITE_DOTS_TOKEN=dots_...
```

---

## CI/CD

| Workflow | Trigger | What it does |
|----------|---------|-------------|
| `test.yml` | push / PR | runs the test suite |
| `deploy-hub.yml` | push to `main` (dots/ changed) | SSHs to the VM, restarts the hub |
| `publish-binary.yml` | GitHub release published | builds macOS arm64 binary, uploads to release assets |
| `deploy-web.yml` | push to `main` (web/ changed) | deploys web app to Vercel |

---

## Development

```bash
# Install all deps
uv sync --extra mac --extra dev

# Run tests
uv run pytest

# Build the worker binary
uv run pyinstaller worker.spec --distpath dist/

# Lint
uv run ruff check dots/
```

---

## Repository layout

```
dots/          Python package — hub, worker, CLI, API
  distributed/ hub.py, worker.py (iroh QUIC transport)
  app/         api.py (aiohttp HTTP server)
  system/      util, geo, accounts
  cli.py       entry point: dots hub / worker / chat / credits
web/           Vite + React web app
  src/
    views/     ChatView
    components/  Globe, ContributePanel, etc.
    lib/       api.ts, config.ts, format.ts
tests/         pytest suite
.github/
  workflows/   CI/CD
worker.spec    PyInstaller spec for worker binary
```

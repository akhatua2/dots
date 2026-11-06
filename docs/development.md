# Development

## Repository layout

This is a monorepo. Each top-level dir has its own README; the high level:

```
apps/              end-user clients
  web/             Vite + React web app (chat + pool map + contribute) → Vercel
  macos/           native SwiftUI macOS app (menu bar, ask panel, pools); see apps/macos/README.md
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
| POST | `/v1/images/generations` | Draw a picture from words (OpenAI's image API) |
| POST | `/v1/images/edits` | Draw from a starting picture and words, as JSON or a multipart form |

With `"stream": true` an image request streams the picture as it forms: an `image_generation.partial_image`
event (`image_edit.` for edits) after each step, then `...completed` with the picture. An edit keeps the starting
picture's own pixels wherever it didn't mean to change anything, unless the request says `"keep_unchanged": false`.
| GET | `/v1/models` | Available models |
| GET | `/v1/credits` | CU balances |
| GET | `/v1/pool/nodes` | Worker map data |
| GET | `/v1/download/worker` | Pre-built macOS worker binary |
| GET | `/healthz` | Liveness + worker count |

Two request headers change how a turn runs: `X-Dots-Account` charges it to an account (otherwise `api`),
and `X-Dots-Speculate: off` makes it decode one token at a time. A streamed answer carries extra `dots`
events that OpenAI clients ignore: `draft` (words guessed ahead, not yet checked), `verified` (how many
guesses a check kept) and `speed` (this answer's speed over the same pool without guessing).

## How a token is written

A turn runs on one Mac when the model fits there, and is split into layer ranges across Macs when it
doesn't. The head Mac holds the first layers and the output layer, and drives every token; each other Mac
runs its layers on the hidden state it is sent (`distributed/worker.py`).

Guessing ahead (`generation/speculative.py`) gets several tokens out of one pass of the big model. Guesses
come from looking the last few tokens up earlier in the conversation, which is free and on for every
turn, and from a draft on the head Mac: the model's own draft head (`backends/mlx/mtp.py`), one extra
layer that reads the hidden states the pool just ran and pays on every turn, or else a small draft model,
which only pays when a turn crosses the network. One pass checks them all and keeps the ones the big model
would have written itself. Checking a few tokens at once goes
through a Metal kernel (`backends/mlx/small_batch.py`) that reads each 4-bit weight once for all of them.
The measurements behind each choice are in [`plans/decode-over-the-network.md`](plans/decode-over-the-network.md).

| Setting | Where | Default | What it does |
|---|---|---|---|
| `DOTS_DRAFT_MODEL` | worker | none | The model's own draft head (an mlx-community `*-MTP-*` repo, or the model's original release, which carries it), or a small model with the same tokenizer for turns that cross Macs |
| `DOTS_DRAFT_TOKENS` | worker | 2 for a head, 4 for a model | Most tokens the draft guesses before a check |
| `DOTS_DRAFT_ADAPTIVE` | worker | on | Guess fewer after misses, more after a full hit |
| `DOTS_PROMPT_LOOKUP` | worker | on | Look guesses up in the conversation |
| `DOTS_SMALL_BATCH_KERNEL` | worker | on | The Metal kernel for checking 2 to 5 tokens |
| `DOTS_PREFILL_CHUNK` | worker | 1024 | Long prompts cross Macs in chunks of this many tokens, overlapping compute and network; 0 sends them whole |
| `DOTS_CHAIN` | worker | `star` | `ring` passes the hidden state Mac to Mac instead of back through the head |
| `DOTS_IMAGE_MAX_PIXELS` | worker | 1048576 | Images larger than this are scaled down before the model reads them |
| `DOTS_IMAGE_MODEL` | worker | none | An image model this Mac draws with, whole; unset, it offers none (`images.py`) |
| `DOTS_IMAGE_COMMAND`, `DOTS_IMAGE_EDIT_COMMAND` | worker | mflux's FLUX 3 commands | The tool that draws, and the one that edits |
| `DOTS_IMAGE_STEPS` | worker | 8 | Denoising steps when a job doesn't say |
| `DOTS_IMAGE_EDIT_PIXELS` | worker | 1048576 | A larger picture is edited at this size and the edit put back into the full-size picture |
| `DOTS_PLACEMENT_RAM_MARGIN_GB` | hub | 1.0 | Memory the head Mac keeps free beyond its share |
| `DOTS_HEAD_WORKSPACE_RAM_GB` | hub | 0.5 | Working memory counted for the head's output layer |

Models that read images (Qwen3.5) take them as OpenAI-style message parts, `{"type": "image_url",
"image_url": {"url": "data:image/png;base64,..."}}`. Only inline images are read; a worker never fetches a
URL. The head Mac runs the vision encoder (`backends/mlx/vision.py`), loaded the first time an image comes,
and the image's rows take the places of its placeholder tokens, so the rest of the pool sees a prompt like
any other.

`scripts/bench_decode.py` compares these on a simulated two-Mac pool, and `scripts/bench_wire.py` times
round trips over the peer transport.

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

## Building the Mac app

```bash
brew install xcodegen
cd apps/macos
xcodegen generate
xcodebuild -project dots.xcodeproj -scheme dots -configuration Debug -derivedDataPath build \
  -destination 'platform=macOS,arch=arm64' CODE_SIGN_IDENTITY="-" DEVELOPMENT_TEAM="" CODE_SIGN_STYLE=Manual build
open build/Build/Products/Debug/dots.app
build/Build/Products/Debug/dots.app/Contents/MacOS/dots --selftest   # checks every story against a mock cloud
```

`project.yml` signs with Sourabh's team. The flags above build it ad hoc on any Mac.

An ad hoc build is a new app to macOS each time, so it asks again for the Desktop folder (the worker runs
from this repo) after every rebuild, and the worker waits until someone clicks Allow. Signing with your
own Apple Development identity keeps the answer across rebuilds:

```bash
security find-identity -v -p codesigning     # the identity's hash; its team is the OU in the certificate
xcodebuild ... CODE_SIGN_IDENTITY="<hash>" DEVELOPMENT_TEAM="<team>" CODE_SIGN_STYLE=Manual build
```

`infra/dev-local.sh` runs the app against a real hub and cloud on this Mac; see the top of that script.
Its contract with the cloud is `apps/macos/API.md`.

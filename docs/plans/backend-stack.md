# Backend stack

Decided November 2026, after the SwiftUI app landed with a mock cloud and its contract (`apps/macos/API.md`).

## What we run

| Part | Stack | Keep? |
| --- | --- | --- |
| Engine: hub and workers | Python 3.11, asyncio, iroh (QUIC) between Macs, MLX on Apple Silicon | Keep |
| Hub HTTP API | aiohttp, OpenAI-compatible, in the hub process | Keep |
| Ledger | Append-only JSON file (`credits.json`), integer micro-CU | Move to SQLite |
| Cloud (people, pools, invites, endorsements, boosts, badges, notes) | Did not exist; the app mocked it | Build: `dots.cloud` |
| Hosting | One VM behind a free ngrok URL | One small box behind a Cloudflare Tunnel |

## Decisions

**One language for the backend: Python.** The engine is Python, and the expensive part of a token (the
matrix work) already runs in native code (MLX, Metal). Python's cost is the per-hop overhead, which we
will measure before deciding to move anything. A second backend language now would double the surface
for two people and buy nothing measurable.

**The cloud is a package beside the hub, not a new service stack.** `dots.cloud` answers the app's
contract (`API.md`) using aiohttp like the hub, reads live pools from the hub's HTTP API, and keeps its
own state in SQLite. It can run in the hub's process or next to it. The app needs one address.

**SQLite, not Postgres.** One writer, one box, small data, WAL mode, a single file to back up. The
ledger moves into the same database later, so credits and spends live in one transaction. Postgres is
the move only when there is more than one cloud instance.

**Not Supabase or Firebase.** They solve auth and hosting we can do in a few hundred lines, and they
would put people's pool data with a third party for no gain.

**Heterogeneous hardware sits behind the backend interface.** `dots.backends` already has MLX and
llama.cpp. Placement, the wire format and the ledger stay hardware-agnostic; a CUDA or ROCm worker is a
new backend, not a new engine.

**Hosting: one always-on box.** The hub does little work per token, so a small VM or a Pi is enough.
A Cloudflare Tunnel gives it a fixed HTTPS name for free and replaces the ngrok URL.

## What changes first

1. `dots.cloud`: the contract in `API.md`, backed by SQLite and the live hub. The app switches to it by
   turning off the mock and entering the address.
2. The model ladder comes from the cloud, not from the app, so a pool's models are whatever the hub serves.
3. Per-person tokens once there is more than one person per cloud.
4. Ledger into SQLite.

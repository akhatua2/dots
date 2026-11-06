# macos

Native macOS client for dots, designed by Sourabh Daroji
(https://sourabhdaroji.com/work/dots-decentralized-inference). Lend this Mac to
a pool, ask the pool, and use it as an OpenAI endpoint, with no terminal.

## What is in it

- **Menu bar dropdown**: a dot in the menu bar (hollow when idle, a breathing
  orange dot while lending). Click it to turn lending on/off, set how much memory
  to lend (the share kept for the owner is hatched), lend only when plugged in,
  pause while the Mac is in use, pause for an hour, and watch credits. Backed by
  the real worker: the bridge starts, stops and resizes it (`--max-ram`).
- **Ask** (Option-Space): a panel over whatever you are reading, with a receipt of
  which Macs answered and why it ran there. Drop a text file on it for context.
- **Explore / Pool / Profile / API** screens, and the join moment.
- Closing the window keeps the app (and lending) alive in the menu bar.
- **Liquid-glass look** (on by default; `glass = false` turns it off in `config.toml`): translucent sidebar, dropdown and
  Ask panel over native macOS vibrancy. The default (`false`) is the flat graphite look from the mockups.

## Data: real vs preview

The UI renders only what the bridge returns (`catalog`, `lend_get`, `usage`).
Real hub data (the pool's Macs, credits, device, memory, join token) is always
used. Features the hub does not serve yet (public pool directory, endorsements,
badges, per-key API access, credits per hour history) come from
`backend/preview.json`, served by the bridge so the whole design is populated.
Turn that off with `preview_data = false` in `config.toml`. Nothing is hardcoded
in the UI. Tuning values live in `config.toml`.


## How it works

The heavy lifting (iroh transport + mlx compute) stays in Python. The app is a
thin [Tauri](https://tauri.app) shell. The UI is **bundled into the app** (loaded
via `tauri://`, not a localhost server), and the Rust shell talks to a Python
**bridge** sidecar over **stdio** with newline-delimited JSON — the same model
VS Code/LSP use for renderer ↔ backend. No TCP port, no socket file.

```
Tauri window (bundled UI, tauri://)
   │  invoke('call'/'chat')        ▲  'chat' events (streamed tokens)
   ▼                               │
Rust shell ──stdin/stdout JSON-RPC──▶ backend/bridge.py (sidecar)
                                        ├─ chat client ──iroh──▶ hub
                                        └─ local worker ──iroh──▶ hub  (contributes)
```

- **Request/response** (`status`, `account`, `usage`, `pool`) → the `call`
  command; the shell forwards it and returns the bridge's reply.
- **Chat** → the `chat` command fires a prompt; tokens stream back as Tauri
  `chat` events.
- The UI has **no effects**: all bridge state lives in an external store read
  via `useSyncExternalStore` (`ui/src/lib/store.ts`).

## Layout

```
backend/bridge.py   the sidecar: stdio JSON-RPC, runs the chat client + worker
ui/                 the renderer (Vite + React) → bundled by Tauri (ui/dist)
src-tauri/          the Rust shell (spawns the bridge, owns the window + IPC)
```

## Develop

Prereqs: Rust (latest stable), Node, and the dots engine synced
(`cd ../../core && uv sync --extra mac`).

```bash
npm install          # Tauri CLI
npm run ui:install   # renderer deps (one-time)
npm run dev          # builds the UI, then `tauri dev`
```

`npm run dev` sets `DOTS_BRIDGE_CMD` to the repo's venv python + `bridge.py`, so
the shell runs the bridge from source. In a packaged build the bridge ships as
the `dots-bridge` sidecar binary instead (see below).

The bridge has a `DOTS_BRIDGE_NO_CONNECT=1` escape hatch that answers stdin
without touching the network — handy for testing the protocol.

## Package (Phase 2, not done yet)

`npm run build` runs `tauri build`. Still to wire:
- Bundle `backend/bridge.py` as a PyInstaller `dots-bridge` sidecar (so no
  Python install is needed) and add it to `bundle.externalBin`.
- Fix the frozen-binary worker spawn (`python -m dots.cli worker` won't work
  inside a PyInstaller binary — re-exec with an arg instead).
- Code-sign + notarize for distribution (needs an Apple Developer account) and
  add the `com.apple.security.network.client` entitlement for the bridge.

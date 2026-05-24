# macos

Native macOS client for dots — chat and contribute compute with no terminal.

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

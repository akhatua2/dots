# macos

Native macOS client (early stub). The goal: one-click contribute and chat for
people who never open a terminal — wrapping the `dots` worker/chat engine from
[`../../core/`](../../core/).

Currently just a backend scaffold (`backend/server.py`). Likely direction:
Tauri (reuses the web UI + iroh in Rust) or a thin Swift shell.

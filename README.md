# dots

Run a large language model across the Apple Silicon Macs you already own.

dots splits a model across multiple machines and runs each part in parallel — so you can run models that are too large or too slow for any single device.

---

## Contributing compute

Open the web app with your invite link and click **Contribute**. Two files download automatically — keep them in the same folder, then double-click `dots-worker` to start.

Your Mac will appear on the pool map once it connects.

**Requirements:** macOS, Apple Silicon (M1 or later), 8 GB+ RAM.

---

## Development

This is a monorepo:

| Dir | What |
|-----|------|
| [`core/`](core/) | the Python engine — hub, worker, chat, CLI, HTTP API |
| [`apps/`](apps/) | end-user clients (`web/`, `macos/`) |
| [`infra/`](infra/) | hosted-hub deploy (systemd, bootstrap) |
| [`docs/`](docs/) | docs + design notes |

See [`docs/development.md`](docs/development.md) for setup, architecture, and contribution guidelines.

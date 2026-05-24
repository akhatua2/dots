# core

The Python engine — everything dots actually runs. The hub (coordinator), the
worker (donates compute), the chat client, and the OpenAI-compatible HTTP API
all live here as the single `dots` package.

```
src/dots/        the package (src layout; imports as `dots`)
  distributed/   hub.py, worker.py — iroh QUIC transport
  app/           api.py — aiohttp HTTP server
  system/        util, geo, accounts
  cli.py         entry point: dots hub / worker / chat / credits
tests/           pytest suite
packaging/       worker.spec + worker_entry.py (PyInstaller binary)
scripts/         bootstrap.sh, sim_scheduler.py
```

## Quick start

```bash
cd core
uv sync --extra mac --extra dev      # mac extra = the mlx compute backend
uv run pytest                        # tests
uv run ruff check src tests          # lint
uv run dots chat                     # connect to the baked-in hub
```

Build the distributable worker binary:

```bash
uv run pyinstaller packaging/worker.spec --distpath dist/
```

See [`../docs/development.md`](../docs/development.md) for the full picture.

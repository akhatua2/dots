---
name: dots-backend
description: How to write and review dots backend code in core/ (the Python engine: hub, worker, ledger, HTTP API, placement) and infra/. Use for any Python, API, accounting, transport, or deploy change in this repo.
---

# dots backend

The engine is the product. The apps and the cloud are packaging around it.

## Tooling

- Python 3.11+, managed with **uv**. Run everything from `core/`. Never write `pip install`.
- `uv run pytest` and `uv run ruff check src tests` must both pass before a change is done.
- asyncio throughout; the HTTP API is aiohttp. `pytest-asyncio` is in auto mode, so write `async def test_...`.
- `from __future__ import annotations` at the top of each module, and type hints on every public function.

## Shape of the code

- Source layout: `src/dots/<area>/`, with tests mirroring it in `tests/<area>/`.
- Each module opens with a docstring saying what it is for and why it exists. Read
  `accounting/ledger.py` for the standard.
- Small modules with one job. Pure logic (placement, accounting, formats) has no I/O so it can be
  tested without a hub.
- Names say what a thing is. Comments explain why. No dead code, no commented-out code.
- No hardcoded settings. Read them from the environment or `dots_home()`, document the default, and
  keep one source of truth. Secrets live in `.env` and never in code or logs.

## Correctness rules specific to dots

- **The hub does as little work per token as it can.** Real compute stays on the workers.
- **The ledger is append-only.** No undo, delete or reset. It is zero-sum: earned equals spent.
- Never block the event loop. Wrap blocking calls in `asyncio.to_thread`.
- Credentials: compare in constant time (`hmac.compare_digest`), store keys only as hashes,
  show a secret exactly once, and gate every `/v1/*` route.
- State files go under `dots_home()` and are written atomically. A corrupt file must not stop the
  hub from starting; recover and say so in a log line.
- Errors use the OpenAI shape `{"error": {"message": ...}}` with the right status. They name the cause.
- Keep the wire format and the HTTP API backward compatible. If a field must change, add a new one.

## Performance work

- Measure first. Time compute, network and GPU sync per hop before optimizing anything.
- Over the internet, decode speed is limited by round trips, not bandwidth. Prefer algorithmic wins
  (speculative decoding, fewer stages, latency-aware placement) before custom kernels.
- A speedup claim needs a before and after number from the same harness.

## Before you call it done

1. Tests for the behaviour, including the failure case, not just the happy path.
2. `uv run pytest` and `uv run ruff check src tests` are clean.
3. If you touched the HTTP API, update its docstring and `docs/`.
4. Run the real thing (hub plus a worker, or `dots chat`) at least once. Passing tests are not enough.

"""Local bridge between the dots macOS app and the dots pool.

This process is a *sidecar*: the Tauri shell spawns it and talks to it over
**stdio** using newline-delimited JSON (one JSON object per line) — the same
shape LSP/DAP servers use. No TCP port, no socket file; the OS tears the child
down with the parent.

It:
  * connects a dots chat client to the hub (baked NodeId + DOTS_JOIN_TOKEN),
  * spawns a local compute worker so this machine contributes (and so a solo
    user has a pool to query),
  * answers requests on stdin and streams chat tokens back on stdout.

Protocol
--------
Requests  (shell → here), one per line on stdin:
    {"id": 1, "method": "status"}            # also: account | usage | pool
    {"method": "chat", "prompt": "...", "max_tokens": 256}

Responses (here → shell), one per line on stdout:
    {"id": 1, "result": {...}}               # reply to an id'd request
    {"id": 1, "error": "..."}
    {"chat": "token",  "text": "..."}        # chat stream
    {"chat": "status", "state": "..."}
    {"chat": "done",   "text": "...", "decode_tokens": N, "model_id": "..."}
    {"chat": "error",  "error": "..."}

Anything that isn't protocol (logs, tracebacks) goes to **stderr** so stdout
stays a clean JSON stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import pathlib
import subprocess
import sys
import time
import tomllib
import urllib.request
import uuid

from dots.system.util import (
    load_dotenv_into_env,
    load_account_alias,
    load_account_email,
    load_account_name,
    load_or_create_account,
)
from dots.cli import DEFAULT_HUB
from dots.distributed.worker import PoolNotReadyError, Worker

# Single source of truth for non-secret defaults — never hardcode these in
# code. Edit apps/macos/config.toml; env vars override per-run; secrets (tokens)
# stay in .env.
_CONFIG_PATH = pathlib.Path(__file__).resolve().parent.parent / "config.toml"


def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[bridge] could not read {_CONFIG_PATH}: {e}", file=sys.stderr)
        return {}


_CONFIG = _load_config()


def _conf(key: str, env: str) -> str:
    """Resolve a setting: env var (per-run override) → config.toml. No literals."""
    val = os.environ.get(env) or _CONFIG.get(key)
    if not val:
        raise RuntimeError(f"missing config '{key}' — set it in config.toml or ${env}")
    return val


MODEL = _conf("model", "DOTS_UI_MODEL")


def _hub_get(path: str) -> dict:
    token = os.environ.get("DOTS_JOIN_TOKEN", "")
    req = urllib.request.Request(
        _conf("hub_http", "DOTS_HUB_HTTP") + path,
        headers={
            "Authorization": f"Bearer {token}",
            "ngrok-skip-browser-warning": "true",
        },
    )
    with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


async def pool_nodes() -> dict:
    """Pool workers with location + a contribution ratio for the map.

    Keyed on account_id (alias is display-only, not unique). The ratio uses
    **lifetime** totals: earned / (earned + spent). None only when the account
    has no activity at all (brand new) so the ring shows neutral.

    Until the hub advertises per-node account_id, a node falls back to *this*
    user's account — correct for the common solo case and once deployed.
    """
    pn = await asyncio.to_thread(_hub_get, "/v1/pool/nodes")
    try:
        cr = await asyncio.to_thread(_hub_get, "/v1/credits")
    except Exception:
        cr = {"accounts": {}}

    accounts = cr.get("accounts") or {}  # keyed by account_id

    nodes = []
    for n in pn.get("nodes", []):
        geo = n.get("geo") or {}
        acc_id = n.get("account_id") or bridge.account_id
        acc = accounts.get(acc_id) or {}
        earned = float(acc.get("earned_cu") or 0)
        spent = float(acc.get("spent_cu") or 0)

        total = earned + spent
        ratio = (earned / total) if total > 0 else None  # None → no activity

        nodes.append(
            {
                "id": n.get("id"),
                "account_id": acc_id,
                "alias": n.get("alias"),
                "ready": n.get("ready"),
                "backend": n.get("backend"),
                "device": n.get("device"),  # populated once workers advertise it
                "lat": geo.get("lat"),
                "lng": geo.get("lng"),
                "city": geo.get("city"),
                "country": geo.get("country"),
                "earned_cu": round(earned, 6),  # lifetime
                "spent_cu": round(spent, 6),
                "ratio": ratio,
            }
        )
    return {"nodes": nodes}

# Single writer for the stdout protocol stream. Called only from the event
# loop, so writes never interleave.
_out_lock = asyncio.Lock()


def log(*args: object) -> None:
    print("[bridge]", *args, file=sys.stderr, flush=True)


async def emit(obj: dict) -> None:
    async with _out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


class Bridge:
    """Owns the chat client + the local compute worker subprocess."""

    def __init__(self) -> None:
        self.chat: Worker | None = None
        self.worker_proc: subprocess.Popen | None = None
        self.account_id = load_or_create_account()
        self.ready = False
        self.error: str | None = None

    async def start(self) -> None:
        # Test/escape hatch: come up answering stdin without touching the
        # network or spawning a worker.
        if os.environ.get("DOTS_BRIDGE_NO_CONNECT"):
            log("DOTS_BRIDGE_NO_CONNECT set — not connecting")
            return
        load_dotenv_into_env()  # picks up DOTS_JOIN_TOKEN from .env
        # Contribute compute: spawn a local worker (its own persistent identity).
        # No hub arg → it resolves the baked-in DEFAULT_HUB.
        self.worker_proc = subprocess.Popen(
            [sys.executable, "-m", "dots.cli", "worker"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ},
        )
        # Chat client: DISTINCT identity (so it doesn't take over the worker)
        # but the SAME account, so the user's earned (worker) and spent (chat)
        # CU land on one account.
        self.chat = Worker(
            DEFAULT_HUB,
            compute=False,
            requested_model_id=MODEL,
            identity=str(uuid.uuid4()),
            account_id=self.account_id,
        )
        try:
            await self.chat.connect()
            self.ready = True
            log("connected to hub; pool ready to query")
        except Exception as e:  # surfaced to the UI via status
            self.error = str(e)
            log("connect failed:", e)

    async def usage(self) -> dict:
        resp = await self.chat.conn.call("credits")
        return resp.get("data", {})

    async def pool(self) -> dict:
        return await self.chat.get_pool_status(MODEL)

    async def stop(self) -> None:
        with contextlib.suppress(Exception):
            if self.chat is not None:
                await self.chat._disconnect()
        if self.worker_proc is not None:
            with contextlib.suppress(Exception):
                self.worker_proc.terminate()


bridge = Bridge()


async def handle_chat(prompt: str, max_tokens: int) -> None:
    if not bridge.ready or bridge.chat is None:
        await emit({"chat": "status", "state": "connecting"})
        return

    # send_prompt's on_token is sync; hand tokens to the loop via a queue so
    # they're emitted in order from this coroutine.
    q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    timing = {"first": None}  # wall-clock of the first decoded token

    def on_tok(t: str) -> None:
        if timing["first"] is None:
            timing["first"] = time.monotonic()
        loop.call_soon_threadsafe(q.put_nowait, ("token", t))

    async def run() -> None:
        try:
            text, stats = await bridge.chat.send_prompt(
                prompt, max_tokens=max_tokens, on_token=on_tok
            )
            await q.put(("done", text, stats))
        except PoolNotReadyError as e:
            await q.put(("status", e.state))
        except Exception as e:  # noqa: BLE001
            await q.put(("error", str(e)))

    task = asyncio.create_task(run())
    while True:
        item = await q.get()
        if item[0] == "token":
            await emit({"chat": "token", "text": item[1]})
        elif item[0] == "done":
            stats = item[2]
            decode = stats.get("decode_tokens") or 0
            # tok/s over the decode phase (first token → now); needs >1 token.
            tps = None
            if timing["first"] and decode > 1:
                elapsed = time.monotonic() - timing["first"]
                if elapsed > 0:
                    tps = round(decode / elapsed, 1)
            await emit(
                {
                    "chat": "done",
                    "text": item[1],
                    "decode_tokens": decode,
                    "model_id": stats.get("model_id"),
                    "tps": tps,
                }
            )
            break
        elif item[0] == "status":
            await emit({"chat": "status", "state": item[1]})
            break
        else:
            await emit({"chat": "error", "error": item[1]})
            break
    await task


async def dispatch(req: dict) -> None:
    method = req.get("method")
    rid = req.get("id")

    if method == "chat":
        await handle_chat(
            (req.get("prompt") or "").strip(), int(req.get("max_tokens", 256))
        )
        return

    # Request/response methods carry an id.
    try:
        if method == "status":
            data = {"ready": bridge.ready, "error": bridge.error, "model": MODEL}
        elif method == "account":
            data = {
                "account_id": bridge.account_id,
                "alias": load_account_alias(),
                "name": load_account_name(),
                "email": load_account_email(),
                "model": MODEL,
            }
        elif method == "usage":
            if not bridge.ready:
                raise RuntimeError("connecting")
            data = await bridge.usage()
        elif method == "pool":
            if not bridge.ready:
                raise RuntimeError("connecting")
            data = await bridge.pool()
        elif method == "nodes":
            data = await pool_nodes()  # hub HTTP; works even before chat connects
        else:
            raise RuntimeError(f"unknown method {method!r}")
        await emit({"id": rid, "result": data})
    except Exception as e:  # noqa: BLE001
        await emit({"id": rid, "error": str(e)})


async def main() -> None:
    # Load .env (DOTS_JOIN_TOKEN etc.) up front so HTTP calls like /nodes work
    # even before the background hub connect finishes.
    load_dotenv_into_env()
    # Connect to the hub in the background so we answer stdin immediately; the
    # UI polls "status" until ready.
    asyncio.create_task(bridge.start())

    reader = asyncio.StreamReader()
    await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
    )

    try:
        while True:
            line = await reader.readline()
            if not line:  # stdin closed → parent gone
                break
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                log("bad request line:", line[:120])
                continue
            # Each request runs concurrently so a long chat doesn't block status.
            asyncio.create_task(dispatch(req))
    finally:
        await bridge.stop()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())

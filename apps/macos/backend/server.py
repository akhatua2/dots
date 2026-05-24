"""Local bridge between the dots desktop UI and the dots pool.

Runs an aiohttp server on 127.0.0.1 that:
  * connects a dots chat client to the hub (baked NodeId + DOTS_JOIN_TOKEN),
  * spawns a local compute worker so this machine contributes — and so a solo
    user has a pool to query,
  * serves the renderer (static files) + a small JSON/WebSocket API.

All iroh/mlx work stays in Python; the Electron app is just the window. The
renderer is served from here too, so the UI is same-origin (no CORS).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from aiohttp import WSMsgType, web

from dots.system.util import (
    load_dotenv_into_env,
    load_account_alias,
    load_account_email,
    load_account_name,
    load_or_create_account,
)
from dots.cli import DEFAULT_HUB
from dots.distributed.worker import PoolNotReadyError, Worker

HERE = Path(__file__).resolve().parent
RENDERER = HERE.parent / "renderer"
MODEL = os.environ.get("DOTS_UI_MODEL", "mlx-community/Qwen2.5-0.5B-Instruct-4bit")
PORT = int(os.environ.get("DOTS_UI_PORT", "8732"))


class Bridge:
    """Owns the chat client + the local compute worker subprocess."""

    def __init__(self) -> None:
        self.chat: Worker | None = None
        self.worker_proc: subprocess.Popen | None = None
        self.account_id = load_or_create_account()
        self.ready = False
        self.error: str | None = None

    async def start(self) -> None:
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
        except Exception as e:  # surfaced to the UI
            self.error = str(e)

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


async def _on_startup(app: web.Application) -> None:
    # Run connect in the background so the server (and UI) come up immediately;
    # the UI polls /api/status until ready.
    asyncio.create_task(bridge.start())


async def _on_cleanup(app: web.Application) -> None:
    await bridge.stop()


async def api_status(_req: web.Request) -> web.Response:
    return web.json_response(
        {"ready": bridge.ready, "error": bridge.error, "model": MODEL}
    )


async def api_account(_req: web.Request) -> web.Response:
    return web.json_response(
        {
            "account_id": bridge.account_id,
            "alias": load_account_alias(),
            "name": load_account_name(),
            "email": load_account_email(),
            "model": MODEL,
        }
    )


async def api_usage(_req: web.Request) -> web.Response:
    if not bridge.ready:
        return web.json_response({"error": "connecting"}, status=503)
    try:
        return web.json_response(await bridge.usage())
    except Exception as e:
        return web.json_response({"error": str(e)}, status=503)


async def api_pool(_req: web.Request) -> web.Response:
    if not bridge.ready:
        return web.json_response({"error": "connecting"}, status=503)
    try:
        return web.json_response(await bridge.pool())
    except Exception as e:
        return web.json_response({"error": str(e)}, status=503)


async def ws_chat(req: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(req)
    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            continue
        data = json.loads(msg.data)
        prompt = (data.get("prompt") or "").strip()
        if not prompt:
            continue
        # Stream tokens in order via a queue (send_prompt's on_token is sync).
        q: asyncio.Queue = asyncio.Queue()

        def on_tok(t: str) -> None:
            q.put_nowait(("token", t))

        async def run() -> None:
            try:
                text, stats = await bridge.chat.send_prompt(
                    prompt, max_tokens=int(data.get("max_tokens", 256)), on_token=on_tok
                )
                q.put_nowait(("done", text, stats))
            except PoolNotReadyError as e:
                q.put_nowait(("status", e.state))
            except Exception as e:  # noqa: BLE001
                q.put_nowait(("error", str(e)))

        task = asyncio.create_task(run())
        while True:
            item = await q.get()
            if item[0] == "token":
                await ws.send_json({"type": "token", "text": item[1]})
            elif item[0] == "done":
                stats = item[2]
                await ws.send_json(
                    {
                        "type": "done",
                        "text": item[1],
                        "decode_tokens": stats.get("decode_tokens"),
                        "model_id": stats.get("model_id"),
                    }
                )
                break
            elif item[0] == "status":
                await ws.send_json({"type": "status", "state": item[1]})
                break
            else:
                await ws.send_json({"type": "error", "error": item[1]})
                break
        await task
    return ws


async def index(_req: web.Request) -> web.FileResponse:
    return web.FileResponse(RENDERER / "index.html")


def make_app() -> web.Application:
    app = web.Application()
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/", index)
    app.router.add_get("/api/status", api_status)
    app.router.add_get("/api/account", api_account)
    app.router.add_get("/api/usage", api_usage)
    app.router.add_get("/api/pool", api_pool)
    app.router.add_get("/api/chat", ws_chat)
    app.router.add_static("/static", str(RENDERER))
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host="127.0.0.1", port=PORT, print=None)

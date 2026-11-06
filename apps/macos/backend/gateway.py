"""A local OpenAI-compatible endpoint for the apps on this Mac.

It runs inside the bridge on 127.0.0.1 and does four small jobs:

  * checks the caller's key (keys are made in the app; only a hash is stored),
  * forwards the request to the pool through the hub's own API,
  * adds a `dots` block to the reply saying where it ran and why,
  * keeps a short log, which the API page turns into "saved this month".

Streaming is answered as one chunk; the hub's non-streaming call is the source.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import pathlib
import secrets
import time
import urllib.request
from collections.abc import Callable

from aiohttp import web

KEY_PREFIX = "dots_sk_live_"
MAX_LOG = 200


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class Gateway:
    def __init__(
        self,
        path: pathlib.Path,
        *,
        port: int,
        upstream: Callable[[], str],  # hub http base, e.g. https://hub.example
        token: Callable[[], str],
        model: str,
        pool_name: str,
        price_in: float,  # USD per million tokens of the model we compare against
        price_out: float,
    ) -> None:
        self.path = path
        self.port = port
        self.upstream = upstream
        self.token = token
        self.model = model
        self.pool_name = pool_name
        self.price_in = price_in
        self.price_out = price_out
        self.keys: list[dict] = []
        self.log: list[dict] = []
        self._runner: web.AppRunner | None = None
        self._load()

    # ---- state -----------------------------------------------------------

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            self.keys, self.log = data.get("keys", []), data.get("log", [])
        except Exception:  # noqa: BLE001 - a missing or damaged file just means a fresh start
            pass

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"keys": self.keys, "log": self.log[-MAX_LOG:]}))

    def create_key(self, name: str, scopes: list[str], monthly_limit: int | None) -> tuple[dict, str]:
        secret = KEY_PREFIX + secrets.token_urlsafe(18)
        record = {
            "id": "key_" + secrets.token_hex(4),
            "name": name or "Untitled",
            "scopes": scopes or ["pools"],
            "monthly_limit": monthly_limit,
            "tail": secret[-4:],
            "hash": _hash(secret),
            "created": time.time(),
            "last_used": None,
            "requests": 0,
        }
        self.keys.append(record)
        self._save()
        return self.public(record), secret

    def revoke_key(self, key_id: str) -> bool:
        kept = [k for k in self.keys if k["id"] != key_id]
        changed = len(kept) != len(self.keys)
        self.keys = kept
        if changed:
            self._save()
        return changed

    @staticmethod
    def public(record: dict) -> dict:
        return {k: v for k, v in record.items() if k != "hash"}

    def _verify(self, secret: str) -> dict | None:
        digest = _hash(secret)
        return next((k for k in self.keys if hmac.compare_digest(k["hash"], digest)), None)

    # ---- what the API page shows ------------------------------------------

    def view(self) -> dict:
        free = [r for r in self.log if r["cost"] == "free"]
        saved = sum(r["saved_usd"] for r in self.log)
        return {
            "base": f"http://localhost:{self.port}/v1",
            "running": self._runner is not None,
            "keys": [self.public(k) for k in self.keys],
            "requests": len(self.log),
            "free_pct": round(100 * len(free) / len(self.log)) if self.log else 0,
            "saved_usd": round(saved, 2),
            "recent": list(reversed(self.log[-8:])),
        }

    # ---- the server -------------------------------------------------------

    async def start(self) -> None:
        app = web.Application(client_max_size=4 * 1024 * 1024)
        app.router.add_get("/v1/models", self._models)
        app.router.add_post("/v1/chat/completions", self._chat)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", self.port).start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def _caller(self, request: web.Request) -> dict | None:
        header = request.headers.get("Authorization", "")
        return self._verify(header[7:].strip()) if header.startswith("Bearer ") else None

    async def _models(self, request: web.Request) -> web.Response:
        if self._caller(request) is None:
            return _error(401, "missing or invalid key")
        return web.json_response({"object": "list", "data": [{"id": "auto", "object": "model", "owned_by": "dots"}]})

    async def _chat(self, request: web.Request) -> web.Response:
        key = self._caller(request)
        if key is None:
            return _error(401, "missing or invalid key")
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _error(400, "request body must be valid JSON")

        started = time.time()
        body["model"] = self.model  # "auto" and the pool's own model both mean the pool
        body["stream"] = False
        try:
            reply = await asyncio.to_thread(self._forward, body)
        except Exception as e:  # noqa: BLE001 - surface the reason; the caller can retry
            return _error(502, f"the pool did not answer: {e}")

        usage = reply.get("usage") or {}
        tokens_in, tokens_out = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        saved = (tokens_in * self.price_in + tokens_out * self.price_out) / 1e6
        reply["dots"] = {
            "ran_on": self.pool_name,
            "why": "ran in your pool, so it was free",
            "credits": 0,
            "ms": int((time.time() - started) * 1000),
        }
        self._record(key, tokens_in, tokens_out, saved)
        return web.json_response(reply)

    def _forward(self, body: dict) -> dict:
        req = urllib.request.Request(
            self.upstream().rstrip("/") + "/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self.token()}",
                "Content-Type": "application/json",
                "ngrok-skip-browser-warning": "true",
            },
        )
        with urllib.request.urlopen(req, timeout=180) as r:  # noqa: S310
            return json.loads(r.read().decode())

    def _record(self, key: dict, tokens_in: int, tokens_out: int, saved_usd: float) -> None:
        key["last_used"] = time.time()
        key["requests"] = int(key.get("requests", 0)) + 1
        self.log.append(
            {
                "at": time.strftime("%I:%M %p").lstrip("0"),
                "key": key["name"],
                "ran": self.pool_name,
                "tokens": f"{tokens_in:,} in · {tokens_out:,} out",
                "cost": "free",
                "saved": f"saved ${saved_usd:.2f}" if saved_usd >= 0.01 else "saved $0",
                "saved_usd": saved_usd,
            }
        )
        self._save()


def _error(status: int, message: str) -> web.Response:
    return web.json_response({"error": {"message": message}}, status=status)

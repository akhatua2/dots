"""OpenAI-compatible HTTP API on top of a `Hub`.

A `dots hub` can optionally expose a second port speaking the chat-
completions subset of the OpenAI API:

    POST /v1/chat/completions   — generate a response (streaming or not)
    GET  /v1/models             — list the served model
    GET  /healthz               — basic liveness probe

Each HTTP request is a *stateless* turn. The hub builds a one-off
`Conversation` from `messages[]`, runs the pool through `Hub.run_turn`,
streams or returns the result, and then resets the conversation on
every worker so per-conv KV caches don't accumulate.

This lets any OpenAI-compatible client (LangChain, Continue, plain curl,
chatbox UIs, etc.) talk to a dots pool with no awareness of how the
weights are sharded behind the scenes.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
import uuid
from typing import TYPE_CHECKING

from aiohttp import web

from .. import ui

if TYPE_CHECKING:
    from ..distributed.hub import Hub

log = logging.getLogger("dots.api")

# Endpoints reachable without a token: liveness probes and CORS preflight.
# Everything under /v1/* is gated by the shared join token (when one is set).
_OPEN_PATHS = frozenset({"/healthz"})

# CORS: the API is meant to be called from a browser app on another origin.
# Auth rides in the Authorization header (not cookies), so a wildcard origin is
# safe — a stranger's page still can't call the pool without the token.
_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, ngrok-skip-browser-warning",
    "Access-Control-Max-Age": "86400",
}


def _bearer(request: web.Request) -> str | None:
    """Extract the bearer token from `Authorization: Bearer <token>`."""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer ") :].strip()
    return None


@web.middleware
async def _auth_cors_middleware(request: web.Request, handler):
    """Gate /v1/* on the shared join token and stamp CORS headers on every
    response (including the OPTIONS preflight a browser sends first).

    The token is the same `DOTS_JOIN_TOKEN` secret that gates the iroh HELLO,
    so the pool has one shared secret. If it is unset (local dev), the API is
    open — matching the iroh gate, which only enforces when a token is set.
    """
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=_CORS_HEADERS)

    token = os.environ.get("DOTS_JOIN_TOKEN")
    if token and request.path not in _OPEN_PATHS:
        provided = _bearer(request)
        if not provided or not hmac.compare_digest(provided, token):
            resp = _error(401, "missing or invalid token")
            resp.headers.extend(_CORS_HEADERS)
            return resp

    response = await handler(request)
    # A streaming response has already flushed its headers in `prepare()` (it
    # sets CORS itself), so only stamp ones we can still mutate.
    if not response.prepared:
        response.headers.extend(_CORS_HEADERS)
    return response


class HttpApi:
    def __init__(self, hub: "Hub", host: str = "0.0.0.0", port: int = 8080):
        self.hub = hub
        self.host = host
        self.port = port
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application(
            client_max_size=4 * 1024 * 1024,
            middlewares=[_auth_cors_middleware],
        )
        app.router.add_post("/v1/chat/completions", self._chat_completions)
        app.router.add_get("/v1/models", self._models)
        app.router.add_get("/v1/credits", self._credits_all)
        app.router.add_get("/v1/credits/{account_id}", self._credits_one)
        app.router.add_get("/v1/pool/nodes", self._pool_nodes)
        app.router.add_get("/v1/download/worker", self._download_worker)
        app.router.add_get("/healthz", self._healthz)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        ui.notice(f"http api listening on http://{self.host}:{self.port}")

    async def serve_forever(self) -> None:
        await self.start()
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            if self._runner is not None:
                await self._runner.cleanup()
            raise

    # ──────────────────────────── handlers ──────────────────────────────

    async def _models(self, _request: web.Request) -> web.Response:
        models = sorted(self.hub.deployments)
        return web.json_response(
            {
                "object": "list",
                "data": [
                    {
                        "id": model_id,
                        "object": "model",
                        "owned_by": "dots",
                    }
                    for model_id in models
                ],
            }
        )

    async def _credits_all(self, _request: web.Request) -> web.Response:
        return web.json_response(self.hub.ledger.as_dict())

    async def _credits_one(self, request: web.Request) -> web.Response:
        account_id = request.match_info["account_id"]
        acc = self.hub.ledger.account(account_id)
        if acc is None:
            return web.json_response(
                {"error": {"message": f"unknown account {account_id}"}},
                status=404,
            )
        from dataclasses import asdict

        return web.json_response(asdict(acc) | {"balance": acc.balance})

    async def _pool_nodes(self, _request: web.Request) -> web.Response:
        """List the compute workers in the pool, with their advertised location.

        Powers the worker map: each entry carries the coarse geo (and public IP)
        the worker chose to advertise, plus what it's doing right now.
        """
        nodes = [
            {
                "id": p.id,
                "alias": p.account_alias,
                "ready": p.ready,
                "backend": p.backend,
                "model": p.requested_model_id,
                "layer_range": list(p.layer_range) if p.layer_range else None,
                "geo": p.geo,
            }
            for p in self.hub.participants.values()
            if p.compute
        ]
        return web.json_response({"nodes": nodes})

    async def _download_worker(self, request: web.Request) -> web.StreamResponse:
        """Serve the pre-built macOS arm64 worker binary.

        The binary lives at ~/.dots/worker/dots-worker on the hub server and
        is uploaded there by the publish-binary CI workflow. Gated by the join
        token so only invited contributors can download it.
        """
        import pathlib

        binary_path = pathlib.Path.home() / ".dots" / "worker" / "dots-worker"
        if not binary_path.is_file():
            return _error(404, "worker binary not available yet")

        size = binary_path.stat().st_size
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": 'attachment; filename="dots-worker"',
                "Content-Length": str(size),
                **_CORS_HEADERS,
            },
        )
        await resp.prepare(request)
        with binary_path.open("rb") as f:
            while chunk := f.read(256 * 1024):
                await resp.write(chunk)
        await resp.write_eof()
        return resp

    async def _healthz(self, _request: web.Request) -> web.Response:
        ready = any(d.pool_ready.is_set() for d in self.hub.deployments.values())
        n_workers = sum(
            1 for p in self.hub.participants.values() if p.compute and p.ready
        )
        return web.json_response(
            {
                "ready": ready,
                "workers": n_workers,
                "models": sorted(self.hub.deployments),
            },
            status=200 if ready else 503,
        )

    async def _chat_completions(self, request: web.Request) -> web.StreamResponse:
        try:
            body = await request.json()
        except Exception:
            return _error(400, "request body must be valid JSON")

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return _error(400, "messages must be a non-empty array")

        max_tokens = int(body.get("max_tokens", 256))
        temperature = float(body.get("temperature", 0.7))
        stream = bool(body.get("stream", False))
        # The hub is model-agnostic — the request must name the model
        # (standard OpenAI `model` field); the hub serves a deployment
        # for it on demand.
        model_id = body.get("model")
        if not isinstance(model_id, str) or not model_id:
            return _error(400, "model must be a non-empty string")

        deployment = await self.hub.ensure_model_started(model_id)
        if not deployment.pool_ready.is_set():
            return _error(503, "pool not ready — no compute workers available")

        # Each HTTP request gets a fresh, stateless Conversation. The hub
        # routes worker COMPUTE responses by conv_id, so a unique id per
        # request keeps requests from stepping on each other's KV caches.
        from ..distributed.hub import Conversation

        conv = Conversation(id=f"http-{uuid.uuid4()}")
        conv.history = list(messages)
        conv.token_count = 0

        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created_at = int(time.time())

        try:
            if stream:
                return await self._stream(
                    request,
                    conv,
                    completion_id,
                    created_at,
                    max_tokens,
                    temperature,
                    model_id,
                )
            return await self._nonstream(
                conv,
                completion_id,
                created_at,
                max_tokens,
                temperature,
                model_id,
            )
        finally:
            # Drop the per-conv KV cache from every worker so stateless
            # requests don't accumulate residue.
            try:
                await self.hub.reset_conversation_on_workers(
                    conv.id,
                    model_id=model_id,
                )
            except Exception:
                log.debug("reset_conversation broadcast failed", exc_info=True)

    # ──────────────────────────── response shapes ───────────────────────

    async def _nonstream(
        self,
        conv,
        completion_id: str,
        created_at: int,
        max_new_tokens: int,
        temperature: float,
        model_id: str,
    ) -> web.Response:
        try:
            text, stats = await self.hub.run_turn(
                conv,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                model_id=model_id,
            )
        except Exception as e:
            log.exception("turn failed")
            return _error(500, f"turn failed: {e}")

        return web.json_response(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created_at,
                "model": model_id,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": int(stats.get("prefill_tokens", 0)),
                    "completion_tokens": int(stats.get("decode_tokens", 0)),
                    "total_tokens": int(stats.get("prefill_tokens", 0))
                    + int(stats.get("decode_tokens", 0)),
                },
            }
        )

    async def _stream(
        self,
        request: web.Request,
        conv,
        completion_id: str,
        created_at: int,
        max_new_tokens: int,
        temperature: float,
        model_id: str,
    ) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                **_CORS_HEADERS,
            },
        )
        await response.prepare(request)

        def chunk(delta: dict, finish_reason: str | None = None) -> bytes:
            payload = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_at,
                "model": model_id,
                "choices": [
                    {
                        "index": 0,
                        "delta": delta,
                        "finish_reason": finish_reason,
                    }
                ],
            }
            return f"data: {json.dumps(payload)}\n\n".encode("utf-8")

        # First chunk: role announcement (OpenAI convention)
        await response.write(chunk({"role": "assistant"}))

        async def emit(text: str) -> None:
            await response.write(chunk({"content": text}))

        try:
            await self.hub.run_turn(
                conv,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                on_token=emit,
                model_id=model_id,
            )
        except Exception as e:
            log.exception("streaming turn failed")
            err = json.dumps({"error": {"message": str(e), "type": "turn_error"}})
            await response.write(f"data: {err}\n\n".encode("utf-8"))
            await response.write(b"data: [DONE]\n\n")
            return response

        await response.write(chunk({}, finish_reason="stop"))
        await response.write(b"data: [DONE]\n\n")
        return response


def _error(status: int, message: str) -> web.Response:
    return web.json_response({"error": {"message": message}}, status=status)

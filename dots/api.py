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
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING

from aiohttp import web

from . import ui

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger("dots.api")


class HttpApi:
    def __init__(self, hub: "Hub", host: str = "0.0.0.0", port: int = 8080):
        self.hub = hub
        self.host = host
        self.port = port
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application(client_max_size=4 * 1024 * 1024)
        app.router.add_post("/v1/chat/completions", self._chat_completions)
        app.router.add_get("/v1/models", self._models)
        app.router.add_get("/v1/credits", self._credits_all)
        app.router.add_get("/v1/credits/{account_id}", self._credits_one)
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
        return web.json_response({
            "object": "list",
            "data": [{
                "id": self.hub.model_id,
                "object": "model",
                "owned_by": "dots",
            }],
        })

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

    async def _healthz(self, _request: web.Request) -> web.Response:
        ready = self.hub._pool_ready.is_set()
        n_workers = sum(1 for p in self.hub.participants.values()
                        if p.compute and p.ready)
        return web.json_response({
            "ready": ready,
            "workers": n_workers,
            "model": self.hub.model_id,
        }, status=200 if ready else 503)

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

        if not self.hub._pool_ready.is_set():
            return _error(503, "pool not ready — no compute workers available")

        # Each HTTP request gets a fresh, stateless Conversation. The hub
        # routes worker COMPUTE responses by conv_id, so a unique id per
        # request keeps requests from stepping on each other's KV caches.
        from .hub import Conversation
        conv = Conversation(id=f"http-{uuid.uuid4()}")
        conv.history = list(messages)
        conv.token_count = 0

        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created_at = int(time.time())

        try:
            if stream:
                return await self._stream(
                    request, conv, completion_id, created_at,
                    max_tokens, temperature,
                )
            return await self._nonstream(
                conv, completion_id, created_at, max_tokens, temperature,
            )
        finally:
            # Drop the per-conv KV cache from every worker so stateless
            # requests don't accumulate residue.
            try:
                await self.hub.reset_conversation_on_workers(conv.id)
            except Exception:
                log.debug("reset_conversation broadcast failed", exc_info=True)

    # ──────────────────────────── response shapes ───────────────────────

    async def _nonstream(
        self, conv, completion_id: str, created_at: int,
        max_new_tokens: int, temperature: float,
    ) -> web.Response:
        try:
            text, stats = await self.hub.run_turn(
                conv, max_new_tokens=max_new_tokens, temperature=temperature,
            )
        except Exception as e:
            log.exception("turn failed")
            return _error(500, f"turn failed: {e}")

        return web.json_response({
            "id": completion_id,
            "object": "chat.completion",
            "created": created_at,
            "model": self.hub.model_id,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": int(stats.get("prefill_tokens", 0)),
                "completion_tokens": int(stats.get("decode_tokens", 0)),
                "total_tokens": int(stats.get("prefill_tokens", 0))
                                + int(stats.get("decode_tokens", 0)),
            },
        })

    async def _stream(
        self, request: web.Request, conv,
        completion_id: str, created_at: int,
        max_new_tokens: int, temperature: float,
    ) -> web.StreamResponse:
        response = web.StreamResponse(status=200, headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })
        await response.prepare(request)

        def chunk(delta: dict, finish_reason: str | None = None) -> bytes:
            payload = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_at,
                "model": self.hub.model_id,
                "choices": [{
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }],
            }
            return f"data: {json.dumps(payload)}\n\n".encode("utf-8")

        # First chunk: role announcement (OpenAI convention)
        await response.write(chunk({"role": "assistant"}))

        async def emit(text: str) -> None:
            await response.write(chunk({"content": text}))

        try:
            await self.hub.run_turn(
                conv, max_new_tokens=max_new_tokens, temperature=temperature,
                on_token=emit,
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

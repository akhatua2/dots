"""Frontier answers paid in credits: the dots gateway.

It calls Anthropic with the cloud's own key (`ANTHROPIC_API_KEY`) and charges the answer in credits at
list price less the discount, from the usage Anthropic reports. Without a key it says so; it never makes
up an answer. `DOTS_GATEWAY_URL` points it elsewhere, for tests.

A streamed request is answered as one piece in the stream's own format: the provider is called whole, so
the charge comes from its real usage.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web

from . import catalog
from .sync import HubView

if TYPE_CHECKING:
    from .server import Cloud

ANTHROPIC_VERSION = "2023-06-01"


def _error(fmt: str, status: int, message: str, kind: str) -> web.Response:
    if fmt == "anthropic":
        return web.json_response({"type": "error", "error": {"type": kind, "message": message}}, status=status)
    return web.json_response({"error": {"message": message, "type": kind}}, status=status)


async def answer(cloud: "Cloud", request: web.Request, *, fmt: str) -> web.StreamResponse:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return _error(fmt, 503, "The gateway has no provider key. Set ANTHROPIC_API_KEY where the cloud runs.", "api_error")
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return _error(fmt, 400, "Send a JSON body.", "invalid_request_error")
    model = str(body.get("model", ""))
    if model not in catalog.TERMS["prices"]:
        return _error(fmt, 400, f"The gateway doesn't serve {model or 'that model'}.", "invalid_request_error")

    # Refuse up front when even a typical answer is more than the balance.
    balance = cloud.you(request).balance(await HubView.read(cloud.hub, cloud.config.pool_model))
    typical, _ = catalog.gateway_credits(model, catalog.TERMS["typicalInput"], catalog.TERMS["typicalOutput"])
    if balance < typical:
        return _error(fmt, 402, f"Not enough credits: about {typical} needed, {int(balance)} left.", "insufficient_credits")

    wants_stream = bool(body.get("stream"))
    upstream = {**body, "stream": False}
    base = os.environ.get("DOTS_GATEWAY_URL", "https://api.anthropic.com").rstrip("/")
    if fmt == "anthropic":
        url, headers = f"{base}/v1/messages", {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    else:
        url, headers = f"{base}/v1/chat/completions", {"Authorization": f"Bearer {key}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=upstream, headers=headers, timeout=aiohttp.ClientTimeout(total=300)) as r:
                reply = await r.json(content_type=None)
                if r.status >= 400:
                    message = (reply.get("error") or {}).get("message", f"provider returned {r.status}")
                    return _error(fmt, r.status, message, "api_error")
    except aiohttp.ClientError as e:
        return _error(fmt, 503, f"The provider didn't answer: {e}", "api_error")

    usage = reply.get("usage") or {}
    tokens_in = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)))
    tokens_out = int(usage.get("output_tokens", usage.get("completion_tokens", 0)))
    credits, list_credits = catalog.gateway_credits(model, tokens_in, tokens_out)
    name = model.replace("claude-", "Claude ").replace("-", " ").title().replace(" 5 5", " 5.5").replace(" 5 1", " 5.1")
    cloud.store.spend(request["caller"].person, credits, f"{name}, through the gateway")
    receipt = {"credits": credits, "list_credits": list_credits, "discount": catalog.TERMS["discount"], "simulated": False}

    if not wants_stream:
        return web.json_response({**reply, "dots": receipt})
    if fmt == "anthropic":
        return await _anthropic_stream(request, reply, receipt)
    return await _openai_stream(request, reply, receipt)


async def _openai_stream(request: web.Request, reply: dict, receipt: dict) -> web.StreamResponse:
    out = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await out.prepare(request)
    text = ((reply.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    base = {"id": reply.get("id", ""), "object": "chat.completion.chunk", "model": reply.get("model", "")}
    for chunk in (
        {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}}]},
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": reply.get("usage")},
        {**base, "choices": [], "dots": receipt},
    ):
        await out.write(f"data: {json.dumps(chunk)}\n\n".encode())
    await out.write(b"data: [DONE]\n\n")
    return out


async def _anthropic_stream(request: web.Request, reply: dict, receipt: dict) -> web.StreamResponse:
    out = web.StreamResponse(headers={"Content-Type": "text/event-stream", "x-dots-credits": str(receipt["credits"]),
                                      "x-dots-simulated": "false"})
    await out.prepare(request)
    text = "".join(b.get("text", "") for b in reply.get("content", []) if b.get("type") == "text")
    events = [
        ("message_start", {"type": "message_start", "message": {**reply, "content": [], "stop_reason": None}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": reply.get("stop_reason", "end_turn")},
                           "usage": {"output_tokens": (reply.get("usage") or {}).get("output_tokens", 0)}}),
        ("message_stop", {"type": "message_stop"}),
        ("dots", receipt),
    ]
    for name, data in events:
        await out.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
    return out

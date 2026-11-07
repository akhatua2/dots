"""Only my Macs: a question marked so runs only when every Mac holding the model is the asker's own."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from dots.app.api import HttpApi
from dots.distributed.hub import Hub


def _hub(placement: list[tuple[str, str]]):
    """A hub whose model is placed on these (worker, account) pairs"""
    ready = asyncio.Event()
    ready.set()
    deployment = SimpleNamespace(pool_ready=ready, current_placement=[(w, 0, 1, i == 0) for i, (w, _) in enumerate(placement)])
    hub = SimpleNamespace(participants={w: SimpleNamespace(account_id=a, compute=True) for w, a in placement})

    async def ensure_model_started(model_id, pool=""):
        return deployment

    hub.ensure_model_started = ensure_model_started
    hub.only_on = lambda d, account: Hub.only_on(hub, d, account)
    return hub


async def _ask(hub, **headers):
    app = web.Application()
    app.router.add_post("/v1/chat/completions", HttpApi(hub)._chat_completions)
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/v1/chat/completions", headers=headers,
                              json={"model": "m", "privacy": "mine", "messages": [{"role": "user", "content": "hi"}]})
        return r.status, await r.json()


async def test_only_my_macs_refuses_a_pool_with_a_friends_mac_in_it():
    status, body = await _ask(_hub([("w1", "me"), ("w2", "ana")]), **{"X-Dots-Account": "me"})
    assert status == 409 and "aren't yours" in body["error"]["message"]


async def test_only_my_macs_needs_to_know_who_is_asking():
    status, _ = await _ask(_hub([("w1", "me")]))
    assert status == 409

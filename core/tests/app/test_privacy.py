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
    hub.want_head_on = lambda model_id, account: None
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


def test_a_turn_goes_only_to_macs_lending_to_the_pool_that_runs_the_model():
    def mac(wid, pool):
        return SimpleNamespace(id=wid, compute=True, ready=True, backend="mlx", pool=pool)

    macs = {m.id: m for m in (mac("home", ""), mac("prism", "prism"), mac("home-2", ""))}
    hub = SimpleNamespace(participants=macs, _model_pool={"qwen": ""})
    hub.in_pool = lambda p, model_id: Hub.in_pool(hub, p, model_id)
    deployment = SimpleNamespace(model_id="qwen", worker_states={}, selected_backend="mlx")
    assert [p.id for p in Hub._ready_in_pool(hub, deployment)] == ["home", "home-2"], "Prism's Mac never answers Home"


def test_a_split_models_head_moves_to_the_mac_of_who_asked_privately():
    def mac(wid, account, pool=""):
        return SimpleNamespace(id=wid, compute=True, account_id=account, pool=pool)

    macs = {m.id: m for m in (mac("w-ana", "ana"), mac("w-me", "me"), mac("w-elsewhere", "bo", pool="prism"))}
    hub = SimpleNamespace(participants=macs, _model_pool={"m": ""}, _head_account={})
    hub.in_pool = lambda p, model_id: Hub.in_pool(hub, p, model_id)
    split = SimpleNamespace(model_id="m", n_layers=24, current_placement=[("w-ana", 0, 12, True), ("w-me", 12, 24, False)])
    assert not Hub._head_misplaced(hub, split), "nobody asked yet: the layout stands"
    Hub.want_head_on(hub, "m", "me")
    assert Hub._head_misplaced(hub, split), "my private question moves the head to my Mac"
    Hub.want_head_on(hub, "m", "ana")
    assert not Hub._head_misplaced(hub, split), "Ana's Mac already has it"
    Hub.want_head_on(hub, "m", "bo")
    assert not Hub._head_misplaced(hub, split), "Bo lends elsewhere, so there is nowhere in this pool to move it"
    whole = SimpleNamespace(model_id="m", n_layers=24, current_placement=[("w-ana", 0, 24, True), ("w-me", 0, 24, True)])
    Hub.want_head_on(hub, "m", "me")
    assert not Hub._head_misplaced(hub, whole), "a whole copy on each Mac already runs my turn on mine"

"""The cloud against a fake hub: the sync's shape, pools, invites, endorsements, lending, and answers."""

from __future__ import annotations

import json
import re

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from dots.cloud.config import CloudConfig
from dots.cloud.hub import HubClient
from dots.cloud.server import Cloud
from dots.cloud.store import Store

ME = "acc-me"
MODEL = "mlx-community/Qwen2.5-0.5B-Instruct-4bit"
WIRE_DATE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


def answers(payload: dict):
    """A route that always answers with the same JSON."""

    async def handler(_request: web.Request) -> web.Response:
        return web.json_response(payload)

    return handler


def fake_hub(*, nodes: list[dict] | None = None) -> web.Application:
    """Answers the four hub calls the cloud makes, the way the real hub's HTTP API does."""
    nodes = nodes if nodes is not None else [
        {"id": "w-me", "account_id": ME, "alias": None, "ready": True, "device": "MacBook Pro · Apple M1 Pro",
         "layer_range": [0, 24], "ram_gb": 6.0},
        {"id": "w-sam", "account_id": "acc-sam", "alias": "Sam", "ready": True, "device": "Mac Studio · M2 Ultra",
         "layer_range": None, "ram_gb": 48.0},
    ]
    ledger = {"accounts": {ME: {"balance_cu": 0.5, "earned_cu": 0.6, "turns_as_worker": 3}},
              "turns": [{"started_iso": "2026-11-05T22:10:00.123+00:00", "credited": {ME: 6025}, "flops_done": 6_025_000_000,
                         "prefill_tokens": 40, "decode_tokens": 120}]}
    app = web.Application()
    app.router.add_get("/v1/pool/nodes", answers({"nodes": nodes}))
    app.router.add_get("/v1/credits", answers(ledger))
    app.router.add_get("/v1/models", answers({"data": [{"id": MODEL, "tok_per_sec": 41.6}]}))

    async def complete(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        assert body["model"] == MODEL, "the cloud must ask the hub for the model it serves"
        if not body.get("stream"):
            return web.json_response({"choices": [{"index": 0, "message": {"role": "assistant", "content": "Hi there"}}],
                                      "usage": {"prompt_tokens": 5, "completion_tokens": 2}})
        out = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await out.prepare(request)
        await out.write(b'data: {"choices":[{"delta":{"content":"Hi"},"index":0}]}\n\n')
        await out.write(b"data: [DONE]\n\n")
        return out

    app.router.add_post("/v1/chat/completions", complete)
    return app


@pytest_asyncio.fixture
async def env(tmp_path):
    hub_server = TestServer(fake_hub())
    await hub_server.start_server()
    config = CloudConfig(hub_url=str(hub_server.make_url("")), pool_name="Home lab", pool_model=MODEL, hub_ticket="node-ticket")
    cloud = Cloud(Store(tmp_path / "cloud.db"), HubClient(config.hub_url), config, me=ME, my_name="Arpan")
    client = TestClient(TestServer(cloud.app()))
    await client.start_server()
    yield client, cloud
    await client.close()
    await hub_server.close()


def dates_in(value) -> list[str]:
    if isinstance(value, dict):
        return [d for k, v in value.items() for d in ([v] if k in {"date", "joinedAt", "createdAt", "expires", "issued", "lendingSince", "starts"} else dates_in(v))]
    if isinstance(value, list):
        return [d for v in value for d in dates_in(v)]
    return []


async def state(client) -> dict:
    r = await client.get("/v1/state")
    assert r.status == 200
    return await r.json()


async def test_sync_has_every_field_the_app_decodes(env):
    client, _ = env
    sync = await state(client)
    assert set(sync) >= {"state", "terms", "invites", "mock"} and sync["mock"] is False
    s = sync["state"]
    for key in ("meID", "thisMacID", "people", "devices", "pools", "credits", "earnedAllTime", "questionsHelped",
                "creditLog", "badges", "networkLendingNow", "endorsedBefore"):
        assert key in s, key
    pool = s["pools"][0]
    for key in ("id", "name", "isPublic", "blurb", "tags", "memberIDs", "macs", "crowdMacs", "crowdLending", "crowdGB",
                "endorsements", "endorsedByMe", "offersIdleToNetwork", "unlocks", "phase", "createdAt", "speedFactor", "demand"):
        assert key in pool, key
    assert all(WIRE_DATE.match(d) for d in dates_in(sync)), "dates must be whole seconds in UTC"


async def test_the_hub_pool_shows_the_hubs_real_macs(env):
    client, cloud = env
    s = (await state(client))["state"]
    hub = next(p for p in s["pools"] if p["id"] == "hub")
    assert hub["name"] == "Home lab"
    assert {m["macID"] for m in hub["macs"]} == {cloud.this_mac, "w-sam"}, "your own worker is shown as this Mac"
    assert {m["lendGB"] for m in hub["macs"]} == {6, 48}
    assert any(p["name"] == "Sam" for p in s["people"])


async def test_credits_and_the_worker_come_from_the_hub(env):
    client, _ = env
    sync = await state(client)
    s = sync["state"]
    assert s["credits"] == 500 and s["earnedAllTime"] == 600 and s["questionsHelped"] == 3
    assert s["creditLog"][0]["kind"] == "lend" and s["creditLog"][0]["amount"] == 6
    assert sync["worker"]["firstLayer"] == 0 and sync["worker"]["lastLayer"] == 23
    assert sync["ladder"]["tiers"][0]["id"] == MODEL, "the model the hub serves is on the ladder"
    assert sync["hub"] == {"online": True, "model": MODEL, "ticket": "node-ticket"}, "a Mac's worker needs the ticket"


async def test_start_join_and_leave_a_pool(env):
    client, _ = env
    r = await client.post("/v1/pools", json={"id": "late-night-lab", "name": "Late Night Lab", "isPublic": False})
    body = await r.json()
    assert r.status == 200 and body["pool"]["name"] == "Late Night Lab"
    assert any(b["id"] == "starter" for b in body["sync"]["state"]["badges"])
    r = await client.post("/v1/pools/late-night-lab/join")
    body = await r.json()
    assert body["macNumber"] == 1 and body["sync"]["state"]["lendingPoolID"] == "late-night-lab"
    r = await client.post("/v1/pools/late-night-lab/leave")
    assert "lendingPoolID" not in (await r.json())["sync"]["state"]


async def test_endorsing_twice_counts_once_and_earns_early_backer(env):
    client, _ = env
    first = await (await client.post("/v1/pools/hub/endorse")).json()
    second = await (await client.post("/v1/pools/hub/endorse")).json()
    assert first["badge"]["title"] == "Early backer" and second["badge"] is None
    assert next(p for p in second["sync"]["state"]["pools"] if p["id"] == "hub")["endorsements"] == 1


async def test_an_invite_says_what_your_mac_would_add_then_joins(env):
    client, _ = env
    await client.post("/v1/pools/hub/invite", json={"code": "late7q"})
    details = await (await client.get("/v1/invites/LATE7Q", params={"memory_gb": "24"})).json()
    assert details["poolID"] == "hub" and details["addGB"] == 16 and details["macs"] == 2
    joined = await (await client.post("/v1/invites/LATE7Q/accept")).json()
    assert joined["poolID"] == "hub"
    assert (await client.get("/v1/invites/NOPE")).status == 404


async def test_heartbeats_record_lending_and_an_unlock(env):
    client, _ = env
    await client.post("/v1/pools", json={"id": "studio", "name": "Studio", "isPublic": False})
    await client.post("/v1/pools/studio/join")
    body = await (await client.post("/hub/v1/heartbeat",
                                     json={"active": True, "lendGB": 10, "seconds": 120, "macKind": "MacBook Air", "memoryGB": 24})).json()
    s = body["sync"]["state"]
    assert body["unlock"] is None, "a pool with no model to serve unlocks nothing, whatever its memory"
    assert s["nights"][0]["minutes"] == 2 and s["hoursLent"] > 0
    assert any(d["kind"] == "MacBook Air" and d["memoryGB"] == 24 for d in s["devices"])



async def test_a_pool_answer_comes_from_the_hub_with_its_macs(env):
    client, _ = env
    reply = await (await client.post("/hub/v1/pools/hub/chat/completions",
                                     json={"model": "Qwen2.5 0.5B", "messages": [{"role": "user", "content": "hi"}]})).json()
    assert reply["choices"][0]["message"]["content"] == "Hi there"
    assert reply["dots"]["simulated"] is False and len(reply["dots"]["macs"]) == 2
    r = await client.post("/hub/v1/pools/hub/chat/completions", json={"model": "x", "stream": True, "messages": []})
    lines = [line for line in (await r.text()).split("\n\n") if line]
    assert json.loads(lines[0][6:])["dots"]["event"] == "macs"
    assert '"content": "Hi"' in lines[1] or '"content":"Hi"' in lines[1]
    assert json.loads(lines[-2][6:])["dots"]["credits"] == 0 and lines[-1] == "data: [DONE]"
    assert (await client.post("/hub/v1/pools/nowhere/chat/completions", json={})).status == 404


async def test_the_gateway_needs_a_key_and_charges_credits(env, monkeypatch):
    client, _ = env
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert (await client.post("/gateway/v1/messages", json={"model": "claude-opus-5-5"})).status == 503

    provider = web.Application()
    provider.router.add_post("/v1/messages", answers(
        {"content": [{"type": "text", "text": "Fine."}], "usage": {"input_tokens": 1500, "output_tokens": 600}}))
    server = TestServer(provider)
    await server.start_server()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("DOTS_GATEWAY_URL", str(server.make_url("")))
    reply = await (await client.post("/gateway/v1/messages", json={"model": "claude-opus-5-5", "messages": []})).json()
    assert reply["dots"]["credits"] == 13 and reply["dots"]["list_credits"] == 18, "API.md's worked example"
    s = (await state(client))["state"]
    assert s["credits"] == 500 - 13 and s["creditLog"][0]["kind"] == "spend"
    await server.close()


async def test_a_token_is_required_when_one_is_set(tmp_path):
    config = CloudConfig(hub_url="http://127.0.0.1:9", token="secret")
    cloud = Cloud(Store(tmp_path / "c.db"), HubClient(config.hub_url), config, me=ME, my_name="A")
    async with TestClient(TestServer(cloud.app())) as client:
        assert (await client.get("/v1/state")).status == 401
        r = await client.get("/v1/state", headers={"Authorization": "Bearer secret"})
        assert r.status == 200, "and a cloud whose hub is down still answers"
        assert (await r.json())["hub"]["online"] is False


@pytest.mark.parametrize("model_id,name", [(MODEL, "Qwen2.5 0.5B"), ("mlx-community/Llama-3.2-3B-Instruct-4bit", "Llama 3.2 3B")])
def test_short_model_names(model_id, name):
    from dots.cloud.catalog import short_name

    assert short_name(model_id) == name


async def test_the_hub_pool_unlocks_its_model_once_this_macs_worker_is_in(tmp_path):
    nodes: list[dict] = []
    hub_server = TestServer(fake_hub(nodes=nodes))
    await hub_server.start_server()
    config = CloudConfig(hub_url=str(hub_server.make_url("")), pool_model=MODEL)
    cloud = Cloud(Store(tmp_path / "c.db"), HubClient(config.hub_url), config, me=ME, my_name="A")
    my_worker = {"id": "w-me", "account_id": ME, "ready": True, "layer_range": [0, 24], "ram_gb": 4.0}
    async with TestClient(TestServer(cloud.app())) as client:
        await client.post("/v1/pools/hub/join")
        beat = {"active": True, "lendGB": 8, "seconds": 5, "macKind": "Mac", "memoryGB": 16}

        async def heartbeat() -> dict:
            return await (await client.post("/hub/v1/heartbeat", json=beat)).json()

        starting = await heartbeat()
        assert starting["unlock"] is None, "a worker still starting lends nothing"
        hub_pool = next(p for p in starting["sync"]["state"]["pools"] if p["id"] == "hub")
        assert [m["state"] for m in hub_pool["macs"]] == ["online"]

        nodes.append(my_worker)
        first = await heartbeat()
        assert first["unlock"]["modelID"] == MODEL, "the served model is an unlock; Llama's rung is not"
        hub_pool = next(p for p in first["sync"]["state"]["pools"] if p["id"] == "hub")
        assert hub_pool["servesModelID"] == MODEL
        assert hub_pool["macs"][0]["lendGB"] == 4, "the pool counts what the worker lends, not the setting"

        nodes.clear()  # the worker restarts
        await heartbeat()
        nodes.append(my_worker)
        again = await heartbeat()
        assert again["unlock"] is None, "a worker coming back doesn't unlock the model again"
        hub_pool = next(p for p in again["sync"]["state"]["pools"] if p["id"] == "hub")
        assert len(hub_pool["unlocks"]) == 1
    await hub_server.close()


async def test_speed_and_tokens_come_from_the_hub(env):
    client, _ = env
    body = await state(client)
    served = next(t for t in body["ladder"]["tiers"] if t["id"] == MODEL)
    assert served["tokPerSec"] == 42, "the served model's speed is what the hub measured"
    night = next(n for n in body["state"]["nights"] if n["poolID"] == "hub") if body["state"].get("nights") else None
    if night is not None:
        assert night["tokens"] == 160


async def test_what_this_mac_earned_in_the_last_hour_comes_from_the_ledger(env, monkeypatch):
    from datetime import datetime, timezone

    from dots.cloud import timefmt

    client, _ = env
    monkeypatch.setattr(timefmt, "now", lambda: datetime(2026, 11, 5, 22, 40, tzinfo=timezone.utc))
    assert (await state(client))["earning"]["lastHour"] == 6, "6,025 micro-CU at 1,000 credits a CU, half an hour ago"
    monkeypatch.setattr(timefmt, "now", lambda: datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc))
    assert (await state(client))["earning"]["lastHour"] == 0

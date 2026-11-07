"""The cloud's HTTP API: the contract in `apps/macos/API.md`.

Every call that changes something answers with the whole sync, so the app sees the result at once.
Pool answers go to the hub; frontier answers go through the gateway (see gateway.py).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import uuid

from aiohttp import web

from . import catalog, gateway, links, timefmt
from .config import CloudConfig
from .hub import HubClient, HubError
from .store import Store, expires_in, is_expired
from .sync import HUB_POOL, HubView, SyncBuilder, hub_pool

_POOL_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def error(status: int, message: str, kind: str = "invalid_request_error") -> web.Response:
    return web.json_response({"error": {"message": message, "type": kind}}, status=status)


class Cloud:
    def __init__(self, store: Store, hub: HubClient, config: CloudConfig, *, me: str, my_name: str) -> None:
        self.store = store
        self.hub = hub
        self.config = config
        self.me = me
        self.this_mac = f"mac-{me}"
        self.sync = SyncBuilder(store, config, me, self.this_mac)
        store.ensure_person(me, my_name)
        # The cloud runs beside this Mac's hub, so it knows the Mac's memory before the first heartbeat.
        if not store.one("select 1 from devices where id = ?", self.this_mac):
            store.set_device(self.this_mac, me, "Mac", _memory_gb())
        store.ensure_pool(HUB_POOL, config.pool_name, me, blurb=config.pool_blurb)
        store.rename_pool(HUB_POOL, config.pool_name)
        store.describe_pool(HUB_POOL, config.pool_blurb)

    # ---- the app -------------------------------------------------------------

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth], client_max_size=8 * 1024 * 1024)
        r = app.router
        r.add_get("/healthz", self.healthz)
        r.add_get("/v1/state", self.state)
        r.add_get("/v1/pricing", self.pricing)
        r.add_get("/v1/models/describe", self.describe_model)
        r.add_get("/v1/notes/latest", self.latest_note)
        r.add_patch("/v1/me", self.rename_me)
        r.add_post("/v1/badges/seen", self.badges_seen)
        r.add_post("/v1/pools", self.create_pool)
        r.add_patch("/v1/pools/{pool}", self.update_pool)
        r.add_post("/v1/pools/{pool}/join", self.join)
        r.add_post("/v1/pools/{pool}/leave", self.leave)
        r.add_post("/v1/pools/{pool}/share", self.set_share)
        r.add_post("/v1/pools/{pool}/endorse", self.endorse)
        r.add_post("/v1/pools/{pool}/invite", self.create_invite)
        r.add_delete("/v1/pools/{pool}/invite", self.delete_invite)
        r.add_post("/v1/pools/{pool}/boost/pledge", self.pledge)
        r.add_delete("/v1/pools/{pool}/boost/pledge", self.unpledge)
        r.add_get("/v1/invites/{code}", self.invite_details)
        r.add_post("/v1/invites/{code}/accept", self.accept_invite)
        r.add_post("/hub/v1/heartbeat", self.heartbeat)
        r.add_post("/hub/v1/pools/{pool}/chat/completions", self.pool_answer)
        r.add_post("/hub/v1/pools/{pool}/images/{kind:generations|edits}", self.pool_picture)
        r.add_post("/gateway/v1/chat/completions", self.gateway_chat)
        r.add_post("/gateway/v1/messages", self.gateway_messages)
        app.on_cleanup.append(lambda _app: self.hub.close())
        return app

    @web.middleware
    async def _auth(self, request: web.Request, handler):
        if self.config.token and request.path != "/healthz":
            header = request.headers.get("Authorization", "")
            given = header[7:].strip() if header.startswith("Bearer ") else request.headers.get("x-api-key", "")
            if not hmac.compare_digest(given, self.config.token):
                return error(401, "Pass the cloud's token as a Bearer token.", "authentication_error")
        return await handler(request)

    async def whole(self, **extra) -> web.Response:
        return web.json_response({**extra, "sync": await self.sync.build(self.hub)})

    def pool_row(self, pool_id: str):
        return self.store.one("select * from pools where id = ?", pool_id)

    # ---- reading -------------------------------------------------------------

    async def healthz(self, _r: web.Request) -> web.Response:
        hub = await HubView.read(self.hub, self.config.pool_model)
        return web.json_response({"ok": True, "hub": hub.online, "model": hub.model})

    async def state(self, _r: web.Request) -> web.Response:
        return web.json_response(await self.sync.build(self.hub))

    async def pricing(self, _r: web.Request) -> web.Response:
        return web.json_response(catalog.TERMS)

    async def latest_note(self, _r: web.Request) -> web.Response:
        note = (await self.sync.build(self.hub)).get("note")
        return web.json_response(note) if note else error(404, "No note yet: this Mac hasn't lent this week.", "not_found_error")

    # ---- you -----------------------------------------------------------------

    async def rename_me(self, request: web.Request) -> web.Response:
        name = str((await _body(request)).get("name", "")).strip()
        if not name:
            return error(400, "Send a name.")
        self.store.set_person(self.me, name[:60])
        return await self.whole()

    async def badges_seen(self, _r: web.Request) -> web.Response:
        self.store.run("update badges set seen = 1")
        return await self.whole()

    async def describe_model(self, request: web.Request) -> web.Response:
        """What a Hugging Face link runs, for starting a pool from it (links.py)."""
        try:
            described = await asyncio.to_thread(links.describe, request.query.get("link", ""))
        except ValueError as e:
            return error(400, str(e), "invalid_request_error")
        return web.json_response(described.as_dict())

    # ---- pools ---------------------------------------------------------------

    async def create_pool(self, request: web.Request) -> web.Response:
        body = await _body(request)
        pool_id, name = str(body.get("id", "")), str(body.get("name", "")).strip()
        if not _POOL_ID.match(pool_id) or not name:
            return error(400, "Send an id (lowercase letters, digits and dashes) and a name.")
        if self.pool_row(pool_id):
            return error(400, f"There's already a pool called {pool_id}.")
        model = None
        if body.get("model"):  # the link it runs, read the way Start a Pool reads it
            try:
                model = await asyncio.to_thread(links.describe, str(body["model"]))
            except ValueError as e:
                return error(400, str(e))
            if model.runs is False:
                return error(400, f"dots can't run {model.name}: {model.why}")
        self.store.ensure_pool(pool_id, name[:60], self.me, is_public=bool(body.get("isPublic")))
        if model is not None:
            self.store.set_pool_model(pool_id, model.id, model.kind)
        self.store.award("starter", "sparkles", "Pool starter", f"Started {name}")
        sync = await self.sync.build(self.hub)
        pool = next(p for p in sync["state"]["pools"] if p["id"] == pool_id)
        return web.json_response({"pool": pool, "sync": sync})

    async def update_pool(self, request: web.Request) -> web.Response:
        pool_id = request.match_info["pool"]
        if not self.pool_row(pool_id):
            return error(404, f"No pool called {pool_id}.", "not_found_error")
        body = await _body(request)
        if "offersIdleToNetwork" in body:
            self.store.run("update pools set offers_idle = ? where id = ?", int(bool(body["offersIdleToNetwork"])), pool_id)
        return await self.whole()

    async def join(self, request: web.Request) -> web.Response:
        body = await _body(request)
        return await self._join(request.match_info["pool"], _gb(body))

    async def _join(self, pool_id: str, gb: int) -> web.Response:
        """Joining lets this Mac ask the pool; a share (`gb` above 0) also lends it that much."""
        if not self.pool_row(pool_id):
            return error(404, f"No pool called {pool_id}.", "not_found_error")
        hub = await HubView.read(self.hub, self.config.pool_model)
        others = [m for m in self.sync.pool_macs(pool_id, hub) if m["macID"] != self.sync.this_mac]
        self.store.add_member(pool_id, self.me)
        self.store.lend(pool_id, gb)
        unlock = self._unlock(pool_id, hub) if gb > 0 else None
        return await self.whole(macNumber=len(others) + 1, poolID=pool_id, unlock=unlock)

    async def leave(self, request: web.Request) -> web.Response:
        pool_id = request.match_info["pool"]
        self.store.remove_member(pool_id, self.me)
        self.store.lend(pool_id, 0)
        return await self.whole()

    async def set_share(self, request: web.Request) -> web.Response:
        """How much this Mac lends to a pool it is in; 0 stops lending there but stays in the pool."""
        pool_id = request.match_info["pool"]
        if not self.store.one("select 1 from members where pool_id = ? and person_id = ?", pool_id, self.me):
            return error(404, "Join the pool before lending to it.", "not_found_error")
        self.store.lend(pool_id, _gb(await _body(request)))
        return await self.whole()

    async def endorse(self, request: web.Request) -> web.Response:
        pool_id = request.match_info["pool"]
        row = self.pool_row(pool_id)
        if not row:
            return error(404, f"No pool called {pool_id}.", "not_found_error")
        if self.store.one("select 1 from endorsements where pool_id = ? and person_id = ?", pool_id, self.me):
            return await self.whole(badge=None)
        macs = len(self.sync.pool_macs(pool_id, await HubView.read(self.hub, self.config.pool_model)))
        self.store.run("insert into endorsements (pool_id, person_id, at, macs_at) values (?, ?, ?, ?)",
                       pool_id, self.me, timefmt.iso(timefmt.now()), macs)
        badge = None
        if macs < 20:
            badge = self.store.award(f"backer-{pool_id}", "checkmark.seal", "Early backer",
                                     f"Endorsed {row['name']} at {macs} {'Mac' if macs == 1 else 'Macs'}")
        return await self.whole(badge=badge)

    async def create_invite(self, request: web.Request) -> web.Response:
        pool_id = request.match_info["pool"]
        code = str((await _body(request)).get("code", "")).strip().upper()
        if not self.pool_row(pool_id) or not re.fullmatch(r"[A-Z0-9]{4,16}", code):
            return error(400, "Send a code of 4 to 16 letters and digits, for a pool that exists.")
        expires = expires_in(7)
        self.store.run("insert or replace into invites (code, pool_id, from_id, expires) values (?, ?, ?, ?)",
                       code, pool_id, self.me, expires)
        return await self.whole(invite={"code": code, "expires": expires})

    async def delete_invite(self, request: web.Request) -> web.Response:
        self.store.run("delete from invites where pool_id = ?", request.match_info["pool"])
        return await self.whole()

    async def invite_details(self, request: web.Request) -> web.Response:
        invite = self.store.one("select * from invites where code = ?", request.match_info["code"].upper())
        if not invite or is_expired(invite["expires"]):
            return error(404, "That invite has expired or never existed.", "not_found_error")
        pool = self.pool_row(invite["pool_id"])
        hub = await HubView.read(self.hub, self.config.pool_model)
        memory = int(request.query.get("memory_gb", "0") or 0)
        add_gb = max(0, memory - max(memory // 4, 8))
        pooled = self.sync.pooled_gb(pool["id"], hub)
        tiers = self.sync.tiers(hub)["tiers"]
        now_model = catalog.best_for(tiers, pooled)
        then_model = catalog.best_for(tiers, pooled + add_gb)
        details = {
            "code": invite["code"], "poolID": pool["id"], "poolName": pool["name"], "blurb": pool["blurb"],
            "fromName": (self.store.one("select name from people where id = ?", invite["from_id"]) or {"name": None})["name"],
            "macs": len(self.sync.pool_macs(pool["id"], hub)), "pooledGB": pooled, "addGB": add_gb,
            "isMember": bool(self.store.one("select 1 from members where pool_id = ? and person_id = ?", pool["id"], self.me)),
        }
        if now_model:
            details["modelNow"] = now_model["name"]
        if then_model and then_model is not now_model:
            details["unlocks"] = then_model["name"]
        else:
            nxt = next((t for t in tiers if t["minGB"] > pooled + add_gb), None)
            if nxt:
                details["nextModel"], details["shortGB"] = nxt["name"], nxt["minGB"] - pooled - add_gb
        return web.json_response({k: v for k, v in details.items() if v is not None})

    async def accept_invite(self, request: web.Request) -> web.Response:
        invite = self.store.one("select * from invites where code = ?", request.match_info["code"].upper())
        if not invite or is_expired(invite["expires"]):
            return error(404, "That invite has expired or never existed.", "not_found_error")
        return await self._join(invite["pool_id"], _gb(await _body(request)))

    async def pledge(self, request: web.Request) -> web.Response:
        pool_id = request.match_info["pool"]
        if not self.store.one("select 1 from boosts where pool_id = ?", pool_id):
            return error(404, "This pool has no boost planned.", "not_found_error")
        gb = int((await _body(request)).get("gb", 0))
        guest = not self.store.one("select 1 from members where pool_id = ? and person_id = ?", pool_id, self.me)
        self.store.run("insert or replace into pledges (pool_id, person_id, gb, guest) values (?, ?, ?, ?)",
                       pool_id, self.me, max(0, gb), int(guest))
        return await self.whole()

    async def unpledge(self, request: web.Request) -> web.Response:
        self.store.run("delete from pledges where pool_id = ? and person_id = ?", request.match_info["pool"], self.me)
        return await self.whole()

    # ---- the hub -------------------------------------------------------------

    async def heartbeat(self, request: web.Request) -> web.Response:
        beat = await _body(request)
        self.store.set_device(self.this_mac, self.me, str(beat.get("macKind") or "Mac"), int(beat.get("memoryGB") or 0))
        hub = await HubView.read(self.hub, self.config.pool_model)
        self.store.record_lending(active=bool(beat.get("active")), seconds=float(beat.get("seconds") or 0))
        unlocks = (self._unlock(share["pool_id"], hub) for share in self.store.shares())
        return await self.whole(unlock=next((u for u in unlocks if u), None))

    def _unlock(self, pool_id: str, hub: HubView) -> dict | None:
        """The first time this Mac's share takes a pool over the line for a model it can really run: record it and
        give the badge. Only models the pool's hub serves count, and a model is unlocked once, so a worker that
        restarts and comes back is not news.
        """
        macs = self.sync.pool_macs(pool_id, hub)
        mine = next((m for m in macs if m["macID"] == self.this_mac and m["state"] == "lending"), None)
        if mine is None:
            return None
        after_gb = sum(m["lendGB"] for m in macs if m["state"] == "lending")
        before_gb = after_gb - mine["lendGB"]
        servable = {hub.model} if pool_id == HUB_POOL else set()
        crossed = [t for t in self.sync.tiers(hub)["tiers"]
                   if t["id"] in servable and before_gb < t["minGB"] <= after_gb
                   and not self.store.one("select 1 from unlocks where pool_id = ? and model_id = ?", pool_id, t["id"])]
        if not crossed:
            return None
        tier = crossed[-1]
        event = {"id": str(uuid.uuid4()).upper(), "poolID": pool_id, "modelID": tier["id"], "byMacID": self.this_mac,
                 "fromGB": before_gb, "toGB": after_gb, "date": timefmt.iso(timefmt.now())}
        self.store.run("insert into unlocks (id, pool_id, model_id, by_mac_id, from_gb, to_gb, date) values (?, ?, ?, ?, ?, ?, ?)",
                       event["id"], pool_id, tier["id"], self.this_mac, before_gb, after_gb, event["date"])
        pool = self.pool_row(pool_id)
        self.store.award(f"unlock-{pool_id}-{tier['id']}", "bolt.fill", "Unlocker", f"Took {pool['name']} to {tier['name']}")
        return event

    async def pool_answer(self, request: web.Request) -> web.StreamResponse:
        """A pool's answer, from its own model on its own Macs, through the hub."""
        pool_id = request.match_info["pool"]
        row = self.pool_row(pool_id)
        if row is None or (pool_id != HUB_POOL and (not row["serves_model_id"] or row["model_kind"] == "pictures")):
            return error(404, "This pool doesn't answer questions.", "not_found_error")
        body = await _body(request)
        hub = await HubView.read(self.hub, self.config.pool_model)
        if not hub.online:
            return error(503, "The hub isn't answering right now.", "api_error")
        model = hub.model if pool_id == HUB_POOL else row["serves_model_id"]
        macs = [{"id": m["macID"], "initial": _initial(self, m["macID"]), "name": _name(self, m["macID"])}
                for m in self.sync.pool_macs(pool_id, hub) if m["state"] == "lending"]
        receipt = {"pool": row["name"], "model": catalog.short_name(model), "credits": 0, "simulated": False}
        body["model"] = model
        body["pool"] = hub_pool(pool_id)
        if not body.get("stream"):
            try:
                reply = await self.hub.complete(body, self.me)
            except HubError as e:
                return error(e.status, str(e), "api_error")
            reply["dots"] = {**receipt, "macs": macs}
            return web.json_response(reply)
        speculate = request.headers.get("X-Dots-Speculate", "").strip().lower() != "off"
        return await _relay(request, self.hub.stream(body, self.me, speculate), first={"event": "macs", "macs": macs, **receipt},
                            last=receipt)

    async def pool_picture(self, request: web.Request) -> web.StreamResponse:
        """A picture from a pool that draws, streamed as it forms, from its own Macs."""
        pool_id = request.match_info["pool"]
        row = self.pool_row(pool_id)
        if row is None or row["model_kind"] != "pictures":
            return error(404, "This pool doesn't draw pictures.", "not_found_error")
        body = {**await _body(request), "pool": hub_pool(pool_id)}
        out = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await out.prepare(request)
        try:
            async for line in self.hub.picture(request.match_info["kind"], body, self.me):
                await out.write(line)
        except HubError as e:
            await out.write(f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'message': str(e)}})}\n\n".encode())
        await out.write_eof()
        return out

    # ---- the gateway -----------------------------------------------------------

    async def gateway_chat(self, request: web.Request) -> web.StreamResponse:
        return await gateway.answer(self, request, fmt="openai")

    async def gateway_messages(self, request: web.Request) -> web.StreamResponse:
        return await gateway.answer(self, request, fmt="anthropic")


async def _relay(request: web.Request, lines, *, first: dict, last: dict) -> web.StreamResponse:
    """Streams an OpenAI-format answer through: a first chunk naming the Macs, the answer, then the receipt."""
    out = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
    await out.prepare(request)
    await out.write(_event({"choices": [{"delta": {}, "index": 0}], "dots": first}))
    try:
        async for line in lines:
            if line.strip() == b"data: [DONE]":
                continue
            await out.write(line)
    except HubError as e:
        await out.write(_event({"error": {"message": str(e), "type": "api_error"}}))
    await out.write(_event({"choices": [], "dots": last}))
    await out.write(b"data: [DONE]\n\n")
    return out


def _event(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def _name(cloud: Cloud, mac_id: str) -> str:
    device = cloud.store.one("select owner_id from devices where id = ?", mac_id)
    owner = device["owner_id"] if device else (cloud.me if mac_id == cloud.this_mac else None)
    person = cloud.store.one("select name from people where id = ?", owner) if owner else None
    return person["name"] if person else "A Mac"


def _initial(cloud: Cloud, mac_id: str) -> str:
    return "You" if mac_id == cloud.this_mac else _name(cloud, mac_id)[:1].upper()


def _memory_gb() -> int:
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30)
    except (ValueError, OSError):
        return 0


async def _body(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _gb(body: dict) -> int:
    """A share's size from a request body: whole GB, 0 when it doesn't say"""
    try:
        return max(0, int(body.get("gb") or 0))
    except (TypeError, ValueError):
        return 0

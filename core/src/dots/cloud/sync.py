"""Builds the sync, `GET /v1/state`: everything the app shows from the cloud, in one answer.

It reads the hub once (its Macs, its ledger, the model it serves) and the store, and shapes them the way
the Mac app decodes them (see `apps/macos/dots/Cloud/CloudAPI.swift`). Fields the app requires are always
present; optional ones are left out when there is nothing to say.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from . import catalog, timefmt
from .config import CloudConfig
from .hub import HubClient, HubError
from .store import Store, is_expired

HUB_POOL = "hub"


def hub_pool(pool_id: str) -> str:
    """The pool's name at the hub: the hub's own pool is its default"""
    return "" if pool_id == HUB_POOL else pool_id


@dataclass
class HubView:
    """One read of the hub. Empty when the hub is down, so the app still opens and says so."""

    nodes: list[dict] = field(default_factory=list)
    ledger: dict = field(default_factory=dict)
    model: str | None = None
    online: bool = False
    tok_per_sec: float = 0.0
    # The image models the pool's Macs draw with, if any offer one
    draws: list[str] = field(default_factory=list)

    @classmethod
    async def read(cls, hub: HubClient, pool_model: str) -> "HubView":
        """`pool_model` is the model the pool asks for; the hub serves it once the first request names it."""
        try:
            nodes = await hub.nodes()
            ledger = await hub.ledger()
            models = await hub.models()
            draws = await hub.image_models()
        except HubError:
            return cls(model=pool_model)
        model = pool_model if pool_model in models or not models else next(iter(models))
        return cls(nodes=nodes, ledger=ledger, model=model, online=True, tok_per_sec=models.get(model, 0.0), draws=draws)

    def layers(self) -> int:
        ends = [n["layer_range"][1] for n in self.nodes if n.get("layer_range")]
        return max(ends) if ends else 0


class SyncBuilder:
    def __init__(self, store: Store, config: CloudConfig, me: str, this_mac: str) -> None:
        self.store = store
        self.config = config
        self.me = me
        self.this_mac = this_mac

    # ---- the ladder and pools ------------------------------------------------

    def tiers(self, hub: HubView) -> dict:
        served = catalog.served_tier(hub.model, hub.layers(), hub.tok_per_sec) if hub.model else None
        return catalog.ladder(served)

    def pool_macs(self, pool_id: str, hub: HubView) -> list[dict]:
        """A pool's Macs: the workers lending to it at the hub, and this Mac while it lends there."""
        macs: list[dict] = []
        # The Macs whose workers lend to this pool; the hub's own pool is its default ("")
        at_hub = hub_pool(pool_id)
        for node in (n for n in hub.nodes if (n.get("pool") or "") == at_hub):
            mac_id = self.this_mac if node.get("account_id") == self.me else node["id"]
            # A worker in the hub is lending, whether or not a model is loaded yet: the hub loads one on the first
            # question, which the pool would never get if it waited for the model before counting the Mac
            macs.append(self._mac(mac_id, round(node.get("ram_gb") or 0), "lending"))
        share = self.store.share(pool_id)
        if share and self.store.lending()["active"] and all(m["macID"] != self.this_mac for m in macs):
            # In the hub's pool this Mac lends only once its worker is in the hub; until then it is starting up and
            # adds nothing, so a worker restarting never moves the pool's level.
            state = "online" if pool_id == HUB_POOL else "lending"
            macs.append(self._mac(self.this_mac, share["gb"], state))
        return macs

    def _mac(self, mac_id: str, lend_gb: int, state: str) -> dict:
        return {"macID": mac_id, "lendGB": lend_gb, "state": state, "joinedAt": self.store.first_seen(mac_id)}

    def pooled_gb(self, pool_id: str, hub: HubView) -> int:
        return sum(m["lendGB"] for m in self.pool_macs(pool_id, hub) if m["state"] == "lending")

    def pool(self, row, hub: HubView) -> dict:
        pool_id = row["id"]
        s = self.store
        members = [r["person_id"] for r in s.rows("select person_id from members where pool_id = ? order by rowid", pool_id)]
        endorsed = s.rows("select person_id, macs_at from endorsements where pool_id = ?", pool_id)
        mine = next((e for e in endorsed if e["person_id"] == self.me), None)
        pool = {
            "id": pool_id,
            "name": row["name"],
            "isPublic": bool(row["is_public"]),
            "blurb": row["blurb"],
            "tags": [],
            "memberIDs": members,
            "macs": self.pool_macs(pool_id, hub),
            "crowdMacs": 0,
            "crowdLending": 0,
            "crowdGB": 0,
            "endorsements": len(endorsed),
            "endorsedByMe": mine is not None,
            "hostID": row["host_id"],
            "offersIdleToNetwork": bool(row["offers_idle"]),
            "unlocks": [
                {"id": u["id"], "modelID": u["model_id"], "byMacID": u["by_mac_id"], "fromGB": u["from_gb"],
                 "toGB": u["to_gb"], "date": u["date"]}
                for u in s.rows("select * from unlocks where pool_id = ? order by date", pool_id)
            ],
            "phase": row["phase"],
            "createdAt": row["created_at"],
            "speedFactor": 1,
            "demand": 0.8,
        }
        if mine is not None:
            pool["endorsedAtMacs"] = mine["macs_at"]
        if pool_id == HUB_POOL and hub.model:
            pool["servesModelID"] = hub.model  # what actually answers here
        elif row["serves_model_id"]:
            pool["servesModelID"] = row["serves_model_id"]
        # What it runs, by name, and whether it writes text or draws pictures
        if pool.get("servesModelID"):
            pool["modelName"] = catalog.short_name(pool["servesModelID"])
            pool["kind"] = row["model_kind"] or "text"
            if row["serves_model_id"] == pool["servesModelID"] and row["model_gb"]:
                pool["modelGB"] = row["model_gb"]  # the size of its weights: what its Macs hold between them
            if pool["kind"] == "pictures":
                pool["drawsWith"] = pool["servesModelID"]  # its Macs draw pictures with it
        invite = s.one("select code, expires from invites where pool_id = ? order by expires desc", pool_id)
        if invite and not is_expired(invite["expires"]):
            pool["invite"] = {"code": invite["code"], "expires": invite["expires"]}
        boost = s.one("select * from boosts where pool_id = ?", pool_id)
        if boost:
            pool["boost"] = {
                "title": boost["title"], "modelID": boost["model_id"], "starts": boost["starts"],
                "hours": boost["hours"], "plannedBy": boost["planned_by"],
                "pledges": [{"personID": p["person_id"], "gb": p["gb"], "guest": bool(p["guest"])}
                            for p in s.rows("select * from pledges where pool_id = ?", pool_id)],
            }
        activity = s.rows("select * from activity where pool_id = ? order by date desc limit 20", pool_id)
        if activity:
            pool["activity"] = [
                {k: v for k, v in {"id": a["id"], "date": a["date"], "personID": a["person_id"], "kind": a["kind"],
                                   "gb": a["gb"], "text": a["text"]}.items() if v is not None}
                for a in activity
            ]
        return pool

    # ---- credits -------------------------------------------------------------

    def my_ledger(self, hub: HubView) -> dict:
        return (hub.ledger.get("accounts") or {}).get(self.me) or {}

    def earned_since(self, hub: HubView, window: timedelta) -> int:
        """Credits this Mac earned in the last `window`, from the ledger's turns."""
        since = timefmt.now() - window
        micro = sum((turn.get("credited") or {}).get(self.me, 0) for turn in hub.ledger.get("turns", [])
                    if timefmt.parse(_wire_date(turn["started_iso"])) >= since)
        return round(micro / 1e6 * self.config.credits_per_cu)

    def credit_log(self, hub: HubView) -> list[dict]:
        """Newest first: a line per day of work your Mac did, and every spend through the gateway."""
        per_cu = self.config.credits_per_cu
        days: dict[str, dict] = {}
        for turn in hub.ledger.get("turns", []):
            earned = (turn.get("credited") or {}).get(self.me)
            if not earned:
                continue
            day = turn["started_iso"][:10]
            entry = days.setdefault(day, {"amount": 0.0, "runs": 0, "flops": 0, "last": turn["started_iso"]})
            entry["amount"] += earned / 1e6 * per_cu
            entry["runs"] += 1
            entry["flops"] += turn.get("flops_done", 0)
            entry["last"] = max(entry["last"], turn["started_iso"])
        log = [
            {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"lend-{day}")).upper(), "date": _wire_date(e["last"]),
             "amount": round(e["amount"]), "note": f"Your Mac answered {e['runs']} {'question' if e['runs'] == 1 else 'questions'}",
             "kind": "lend", "poolID": HUB_POOL, "computePF": round(e["flops"] / 1e15, 4)}
            for day, e in days.items()
        ]
        log += [{"id": r["id"], "date": r["date"], "amount": r["amount"], "note": r["note"], "kind": "spend"}
                for r in self.store.rows("select * from spends")]
        return sorted(log, key=lambda e: e["date"], reverse=True)

    def balance(self, hub: HubView) -> float:
        spent = self.store.one("select coalesce(sum(amount), 0) as total from spends")["total"]
        return round(float(self.my_ledger(hub).get("balance_cu") or 0) * self.config.credits_per_cu + spent)

    # ---- the whole sync --------------------------------------------------------

    async def build(self, hub_client: HubClient) -> dict:
        hub = await HubView.read(hub_client, self.config.pool_model)
        s = self.store
        mine = self.my_ledger(hub)
        lending = s.lending()
        pools = [self.pool(row, hub) for row in s.rows("select * from pools order by created_at")]

        people = {r["id"]: r["name"] for r in s.rows("select id, name from people")}
        devices = [{"id": d["id"], "ownerID": d["owner_id"], "kind": d["kind"], "memoryGB": d["memory_gb"]}
                   for d in s.rows("select * from devices")]
        for node in hub.nodes:
            owner = node.get("account_id") or node["id"]
            if owner == self.me:
                continue
            people.setdefault(owner, node.get("alias") or "Someone")
            devices.append({"id": node["id"], "ownerID": owner, "kind": (node.get("device") or "Mac").split(" · ")[0],
                            "memoryGB": round(node.get("ram_gb") or 0)})

        state = {
            "meID": self.me,
            "thisMacID": self.this_mac,
            "people": [{"id": pid, "name": name} for pid, name in people.items()],
            "devices": devices,
            "pools": pools,
            "credits": self.balance(hub),
            "earnedAllTime": round(float(mine.get("earned_cu") or 0) * self.config.credits_per_cu),
            "questionsHelped": int(mine.get("turns_as_worker") or 0),
            "creditLog": self.credit_log(hub),
            "badges": [{"id": b["id"], "symbol": b["symbol"], "title": b["title"], "detail": b["detail"],
                        "date": b["date"], "isNew": not b["seen"]} for b in s.rows("select * from badges order by date desc")],
            "networkLendingNow": sum(1 for n in hub.nodes if n.get("ready")),
            "endorsedBefore": 0,
            "hoursLent": round(lending["seconds"] / 3600, 2),
            "computePF": round(float(mine.get("earned_cu") or 0) / 1000, 4),
            "nights": self.nights(hub),
        }
        # Each pool this Mac lends to, how much, and the pool its worker names at the hub
        state["lendingShares"] = [{"poolID": r["pool_id"], "gb": r["gb"], "since": r["since"],
                                   "hubPool": hub_pool(r["pool_id"])} for r in s.shares()]

        sync = {
            "state": state,
            "terms": catalog.TERMS,
            "invites": [],
            "mock": False,
            "ladder": self.tiers(hub),
            "hub": {"online": hub.online, "model": hub.model, "ticket": self.config.hub_ticket or None},
            "earning": {"lastHour": self.earned_since(hub, timedelta(hours=1))},
        }
        worker = self.worker(hub)
        if worker:
            sync["worker"] = worker
        note = self.note(state)
        if note:
            sync["note"] = note
        return sync

    def nights(self, hub: HubView) -> list[dict]:
        runs: dict[str, int] = {}
        tokens: dict[str, int] = {}
        for turn in hub.ledger.get("turns", []):
            if (turn.get("credited") or {}).get(self.me):
                night = timefmt.iso(timefmt.night_of(timefmt.parse(_wire_date(turn["started_iso"]))))
                runs[night] = runs.get(night, 0) + 1
                tokens[night] = tokens.get(night, 0) + int(turn.get("prefill_tokens") or 0) + int(turn.get("decode_tokens") or 0)
        out = []
        for n in self.store.rows("select * from nights order by date"):
            entry = {"date": n["date"], "poolID": n["pool_id"], "minutes": round(n["minutes"], 2),
                     "runs": runs.get(n["date"], 0) if n["pool_id"] == HUB_POOL else 0,
                     "tokens": tokens.get(n["date"], 0) if n["pool_id"] == HUB_POOL else 0}
            if n["first"]:
                entry["from"] = n["first"]
            if n["last"]:
                entry["to"] = n["last"]
            out.append(entry)
        return out

    def worker(self, hub: HubView) -> dict | None:
        """This Mac's place in the hub's pool: the layers it holds, once the hub has placed it."""
        mine = next((n for n in hub.nodes if n.get("account_id") == self.me and n.get("layer_range")), None)
        if mine is None or not hub.model:
            return None
        first, end = mine["layer_range"]
        return {"poolID": HUB_POOL, "poolName": self.config.pool_name, "model": catalog.short_name(hub.model),
                "firstLayer": first, "lastLayer": end - 1, "layers": hub.layers(), "working": bool(mine.get("serving")), "network": False}

    def note(self, state: dict) -> dict | None:
        """The weekly note: what this Mac did in the last seven days. None in a week it didn't lend."""
        since = timefmt.now() - timedelta(days=7)
        week = [n for n in state["nights"] if timefmt.parse(n["date"]) >= since]
        minutes = sum(n["minutes"] for n in week)
        if minutes < 1:
            return None
        earned = sum(e["amount"] for e in state["creditLog"] if e["kind"] == "lend" and timefmt.parse(e["date"]) >= since)
        spent = -sum(e["amount"] for e in state["creditLog"] if e["kind"] == "spend" and timefmt.parse(e["date"]) >= since)
        hours = round(minutes / 60, 1)
        runs = sum(n["runs"] for n in week)
        return {
            "id": f"note-{timefmt.iso(timefmt.night_of(timefmt.now()))[:10]}",
            "issued": timefmt.iso(timefmt.now()),
            "hoursLent": hours,
            "computePF": state["computePF"],
            "creditsEarned": int(earned),
            "creditsSpent": int(spent),
            "questionsHelped": runs,
            "pools": [{"name": self.config.pool_name, "answered": runs, "sharePercent": 100}] if runs else [],
            "badges": [],
            "headline": f"Your Mac lent {hours:g} {'hour' if hours == 1 else 'hours'} this week "
                        f"and earned {int(earned)} {'credit' if int(earned) == 1 else 'credits'}.",
        }


def _wire_date(text: str) -> str:
    """The ledger's '2026-05-24T00:36:18+00:00' (maybe with fractions) as the wire's '2026-05-24T00:36:18Z'."""
    return text[:19] + "Z"

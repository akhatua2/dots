"""The cloud's own state, in one SQLite file: everything about pools and people the hub doesn't keep.

Pools' Macs, credits earned and answers come from the hub. This keeps the rest: pool names and members,
invites, endorsements, boosts and pledges, activity, badges, spends through the gateway, where this Mac
lends and the nights it lent. Writes are small and come from one process, so one connection, used from
the event loop's thread, is enough.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from . import timefmt

_SCHEMA = """
create table if not exists people (id text primary key, name text not null);
create table if not exists devices (
    id text primary key, owner_id text not null, kind text not null, memory_gb integer not null
);
create table if not exists pools (
    id text primary key, name text not null, is_public integer not null, blurb text not null,
    host_id text, created_at text not null, offers_idle integer not null default 0,
    serves_model_id text, phase real not null
);
create table if not exists members (pool_id text, person_id text, primary key (pool_id, person_id));
create table if not exists macs_seen (mac_id text primary key, first_seen text not null);
create table if not exists invites (code text primary key, pool_id text not null, from_id text not null, expires text not null);
create table if not exists endorsements (
    pool_id text, person_id text, at text not null, macs_at integer not null, primary key (pool_id, person_id)
);
create table if not exists boosts (
    pool_id text primary key, title text not null, model_id text not null, starts text not null,
    hours real not null, planned_by text not null
);
create table if not exists pledges (pool_id text, person_id text, gb integer not null, guest integer not null,
    primary key (pool_id, person_id));
create table if not exists activity (
    id text primary key, pool_id text not null, date text not null, person_id text, kind text not null,
    gb integer, text text
);
create table if not exists unlocks (
    id text primary key, pool_id text not null, model_id text not null, by_mac_id text not null,
    from_gb integer not null, to_gb integer not null, date text not null
);
create table if not exists badges (
    id text primary key, symbol text not null, title text not null, detail text not null,
    date text not null, seen integer not null default 0
);
create table if not exists spends (id text primary key, date text not null, amount integer not null, note text not null);
create table if not exists lending (
    id integer primary key check (id = 1), pool_id text, since text, lend_gb integer not null default 0,
    active integer not null default 0, seconds real not null default 0
);
create table if not exists nights (
    date text not null, pool_id text not null, minutes real not null, runs integer not null,
    first text, last text, primary key (date, pool_id)
);
"""


class Store:
    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode = wal")
        self.db.executescript(_SCHEMA)
        self.db.execute("insert or ignore into lending (id) values (1)")

    def rows(self, sql: str, *args) -> list[sqlite3.Row]:
        return self.db.execute(sql, args).fetchall()

    def one(self, sql: str, *args) -> sqlite3.Row | None:
        return self.db.execute(sql, args).fetchone()

    def run(self, sql: str, *args) -> None:
        self.db.execute(sql, args)

    # ---- people and devices ------------------------------------------------

    def set_person(self, person_id: str, name: str) -> None:
        self.run("insert into people (id, name) values (?, ?) on conflict(id) do update set name = excluded.name",
                 person_id, name)

    def ensure_person(self, person_id: str, name: str) -> None:
        self.run("insert or ignore into people (id, name) values (?, ?)", person_id, name)

    def set_device(self, device_id: str, owner_id: str, kind: str, memory_gb: int) -> None:
        self.run(
            "insert into devices (id, owner_id, kind, memory_gb) values (?, ?, ?, ?) "
            "on conflict(id) do update set kind = excluded.kind, memory_gb = excluded.memory_gb",
            device_id, owner_id, kind, memory_gb,
        )

    def first_seen(self, mac_id: str) -> str:
        self.run("insert or ignore into macs_seen (mac_id, first_seen) values (?, ?)", mac_id, timefmt.iso(timefmt.now()))
        return self.one("select first_seen from macs_seen where mac_id = ?", mac_id)["first_seen"]

    # ---- pools ---------------------------------------------------------------

    def ensure_pool(self, pool_id: str, name: str, host_id: str, *, is_public: bool = False, blurb: str = "") -> None:
        self.run(
            "insert or ignore into pools (id, name, is_public, blurb, host_id, created_at, phase) values (?, ?, ?, ?, ?, ?, ?)",
            pool_id, name, int(is_public), blurb, host_id, timefmt.iso(timefmt.now()), _phase(pool_id),
        )
        self.run("insert or ignore into members (pool_id, person_id) values (?, ?)", pool_id, host_id)

    def rename_pool(self, pool_id: str, name: str) -> None:
        self.run("update pools set name = ? where id = ?", name, pool_id)

    def add_member(self, pool_id: str, person_id: str) -> None:
        self.run("insert or ignore into members (pool_id, person_id) values (?, ?)", pool_id, person_id)

    def remove_member(self, pool_id: str, person_id: str) -> None:
        self.run("delete from members where pool_id = ? and person_id = ?", pool_id, person_id)

    def add_activity(self, pool_id: str, kind: str, *, person_id: str | None = None, gb: int | None = None,
                     text: str | None = None) -> None:
        self.run("insert into activity (id, pool_id, date, person_id, kind, gb, text) values (?, ?, ?, ?, ?, ?, ?)",
                 str(uuid.uuid4()), pool_id, timefmt.iso(timefmt.now()), person_id, kind, gb, text)

    # ---- badges and spends ---------------------------------------------------

    def award(self, badge_id: str, symbol: str, title: str, detail: str) -> dict | None:
        """Gives a badge once. Returns it the first time, None after."""
        if self.one("select 1 from badges where id = ?", badge_id):
            return None
        date = timefmt.iso(timefmt.now())
        self.run("insert into badges (id, symbol, title, detail, date) values (?, ?, ?, ?, ?)",
                 badge_id, symbol, title, detail, date)
        return {"id": badge_id, "symbol": symbol, "title": title, "detail": detail, "date": date, "isNew": True}

    def spend(self, amount: int, note: str) -> None:
        self.run("insert into spends (id, date, amount, note) values (?, ?, ?, ?)",
                 str(uuid.uuid4()).upper(), timefmt.iso(timefmt.now()), -abs(amount), note)

    # ---- lending -------------------------------------------------------------

    def lending(self) -> sqlite3.Row:
        return self.one("select * from lending where id = 1")

    def lend_to(self, pool_id: str | None) -> None:
        since = timefmt.iso(timefmt.now()) if pool_id else None
        self.run("update lending set pool_id = ?, since = ? where id = 1", pool_id, since)

    def record_lending(self, *, active: bool, lend_gb: int, seconds: float) -> None:
        """A heartbeat: while this Mac lends, its time counts toward tonight in its pool."""
        self.run("update lending set active = ?, lend_gb = ? where id = 1", int(active), lend_gb)
        row = self.lending()
        if not active or not row["pool_id"] or seconds <= 0:
            return
        self.run("update lending set seconds = seconds + ? where id = 1", seconds)
        now = timefmt.now()
        night = timefmt.iso(timefmt.night_of(now))
        stamp = timefmt.iso(now)
        self.run(
            "insert into nights (date, pool_id, minutes, runs, first, last) values (?, ?, ?, 0, ?, ?) "
            "on conflict(date, pool_id) do update set minutes = minutes + excluded.minutes, last = excluded.last",
            night, row["pool_id"], seconds / 60, stamp, stamp,
        )


def _phase(pool_id: str) -> float:
    """A stable number in [0, 1) per pool, for its emblem."""
    return (uuid.uuid5(uuid.NAMESPACE_URL, pool_id).int % 1000) / 1000


def expires_in(days: int) -> str:
    return timefmt.iso(timefmt.now() + timedelta(days=days))


def is_expired(text: str, at: datetime | None = None) -> bool:
    return timefmt.parse(text) <= (at or timefmt.now())

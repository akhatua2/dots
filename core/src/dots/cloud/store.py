"""The cloud's own state, in one SQLite file: everything about pools and people the hub doesn't keep.

Pools' Macs, credits earned and answers come from the hub. This keeps the rest: pool names and members,
invites, endorsements, boosts and pledges, activity, and for each person their badges and spends through the
gateway, and for each Mac where it lends and the nights it lent. Writes are small and come from one process,
so one connection, used from the event loop's thread, is enough.

A cloud serves several people: its owner, and friends who joined through an invite, each with a token of their
own (kept only as a hash). Older databases had one person's lending, badges and nights in single rows; the
owner's are moved into the per-person tables once (`adopt_owner`).
"""

from __future__ import annotations

import hashlib
import secrets
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
    serves_model_id text, model_kind text, model_gb real, model_original text, phase real not null
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
create table if not exists shares (pool_id text primary key, gb integer not null, since text not null);
create table if not exists nights (
    date text not null, pool_id text not null, minutes real not null, runs integer not null,
    first text, last text, primary key (date, pool_id)
);
create table if not exists tokens (hash text primary key, person_id text not null, mac_id text not null, made text not null);
create table if not exists mac_lending (mac_id text primary key, active integer not null default 0,
    seconds real not null default 0);
create table if not exists mac_shares (mac_id text not null, pool_id text not null, gb integer not null, since text not null,
    primary key (mac_id, pool_id));
create table if not exists mac_nights (
    mac_id text not null, date text not null, pool_id text not null, minutes real not null, runs integer not null,
    first text, last text, primary key (mac_id, date, pool_id)
);
create table if not exists person_badges (
    person_id text not null, id text not null, symbol text not null, title text not null, detail text not null,
    date text not null, seen integer not null default 0, primary key (person_id, id)
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
        self._add_missing_columns()
        self.db.execute("insert or ignore into lending (id) values (1)")
        self._move_old_lending()

    # Columns added since a database was first made: (table, column, type)
    _ADDED = [("pools", "model_kind", "text"), ("pools", "model_gb", "real"), ("pools", "model_original", "text"),
              ("spends", "person_id", "text")]

    def _add_missing_columns(self) -> None:
        for table, column, kind in self._ADDED:
            have = {row["name"] for row in self.rows(f"pragma table_info({table})")}
            if column not in have:
                self.db.execute(f"alter table {table} add column {column} {kind}")

    def _move_old_lending(self) -> None:
        """A database from when a Mac lent to one pool keeps that pool in `lending`: it becomes a share."""
        row = self.one("select * from lending where id = 1")
        if row["pool_id"]:
            self.run("insert or ignore into shares (pool_id, gb, since) values (?, ?, ?)",
                     row["pool_id"], row["lend_gb"], row["since"] or timefmt.iso(timefmt.now()))
            self.run("update lending set pool_id = null, since = null where id = 1")

    def adopt_owner(self, person_id: str, mac_id: str) -> None:
        """Moves what an older, one-person database kept in single rows to its owner and the owner's Mac."""
        old = self.one("select active, seconds from lending where id = 1")
        if old["seconds"] or old["active"]:
            self.run("insert or ignore into mac_lending (mac_id, active, seconds) values (?, ?, ?)",
                     mac_id, old["active"], old["seconds"])
            self.run("update lending set active = 0, seconds = 0 where id = 1")
        self.run("insert or ignore into mac_shares (mac_id, pool_id, gb, since) select ?, pool_id, gb, since from shares",
                 mac_id)
        self.run("delete from shares")
        self.run("insert or ignore into mac_nights (mac_id, date, pool_id, minutes, runs, first, last) "
                 "select ?, date, pool_id, minutes, runs, first, last from nights", mac_id)
        self.run("delete from nights")
        self.run("insert or ignore into person_badges (person_id, id, symbol, title, detail, date, seen) "
                 "select ?, id, symbol, title, detail, date, seen from badges", person_id)
        self.run("delete from badges")
        self.run("update spends set person_id = ? where person_id is null", person_id)

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

    def set_pool_model(self, pool_id: str, model_id: str, kind: str, gb: float | None = None,
                       original: str | None = None) -> None:
        """What a pool runs: one model, which writes text or draws pictures, the size of its weights, and the model
        it was made from"""
        self.run("update pools set serves_model_id = ?, model_kind = ?, model_gb = ?, model_original = ? where id = ?",
                 model_id, kind, gb, original, pool_id)

    def describe_pool(self, pool_id: str, blurb: str) -> None:
        self.run("update pools set blurb = ? where id = ?", blurb, pool_id)

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

    def award(self, person_id: str, badge_id: str, symbol: str, title: str, detail: str) -> dict | None:
        """Gives a person a badge once. Returns it the first time, None after."""
        if self.one("select 1 from person_badges where person_id = ? and id = ?", person_id, badge_id):
            return None
        date = timefmt.iso(timefmt.now())
        self.run("insert into person_badges (person_id, id, symbol, title, detail, date) values (?, ?, ?, ?, ?, ?)",
                 person_id, badge_id, symbol, title, detail, date)
        return {"id": badge_id, "symbol": symbol, "title": title, "detail": detail, "date": date, "isNew": True}

    def badges(self, person_id: str) -> list[sqlite3.Row]:
        return self.rows("select * from person_badges where person_id = ? order by date desc", person_id)

    def spend(self, person_id: str, amount: int, note: str) -> None:
        self.run("insert into spends (id, date, amount, note, person_id) values (?, ?, ?, ?, ?)",
                 str(uuid.uuid4()).upper(), timefmt.iso(timefmt.now()), -abs(amount), note, person_id)

    def spends(self, person_id: str) -> list[sqlite3.Row]:
        return self.rows("select * from spends where person_id = ?", person_id)

    # ---- tokens --------------------------------------------------------------

    def make_token(self, person_id: str, mac_id: str) -> str:
        """A new token for a person's Mac. Only its hash is kept, so it is shown once."""
        token = "dots_" + secrets.token_urlsafe(32)
        self.run("insert into tokens (hash, person_id, mac_id, made) values (?, ?, ?, ?)",
                 _hash(token), person_id, mac_id, timefmt.iso(timefmt.now()))
        return token

    def token_owner(self, token: str) -> tuple[str, str] | None:
        """(person, Mac) a token was made for, or None"""
        row = self.one("select person_id, mac_id from tokens where hash = ?", _hash(token))
        return (row["person_id"], row["mac_id"]) if row else None

    # ---- lending -------------------------------------------------------------
    # A Mac lends an amount to each of any number of pools (its shares). Whether it is lending right now, and for
    # how long it has, is one row per Mac: its rules (plugged in, not in use) apply to all its shares at once.

    def lending(self, mac_id: str) -> dict:
        row = self.one("select active, seconds from mac_lending where mac_id = ?", mac_id)
        return dict(row) if row else {"active": 0, "seconds": 0.0}

    def shares(self, mac_id: str) -> list[sqlite3.Row]:
        return self.rows("select * from mac_shares where mac_id = ? order by since", mac_id)

    def share(self, mac_id: str, pool_id: str) -> sqlite3.Row | None:
        return self.one("select * from mac_shares where mac_id = ? and pool_id = ?", mac_id, pool_id)

    def lend(self, mac_id: str, pool_id: str, gb: int) -> None:
        """Lend `gb` to a pool, or change how much; 0 stops lending to it."""
        if gb <= 0:
            self.run("delete from mac_shares where mac_id = ? and pool_id = ?", mac_id, pool_id)
            return
        self.run("insert into mac_shares (mac_id, pool_id, gb, since) values (?, ?, ?, ?) "
                 "on conflict(mac_id, pool_id) do update set gb = excluded.gb",
                 mac_id, pool_id, gb, timefmt.iso(timefmt.now()))

    def record_lending(self, mac_id: str, *, active: bool, seconds: float) -> None:
        """A heartbeat: while a Mac lends, its time counts toward tonight in each pool it lends to."""
        self.run("insert into mac_lending (mac_id, active) values (?, ?) "
                 "on conflict(mac_id) do update set active = excluded.active", mac_id, int(active))
        shares = self.shares(mac_id)
        if not active or not shares or seconds <= 0:
            return
        self.run("update mac_lending set seconds = seconds + ? where mac_id = ?", seconds, mac_id)
        now = timefmt.now()
        night = timefmt.iso(timefmt.night_of(now))
        stamp = timefmt.iso(now)
        for share in shares:
            self.run(
                "insert into mac_nights (mac_id, date, pool_id, minutes, runs, first, last) values (?, ?, ?, ?, 0, ?, ?) "
                "on conflict(mac_id, date, pool_id) do update set minutes = minutes + excluded.minutes, last = excluded.last",
                mac_id, night, share["pool_id"], seconds / 60, stamp, stamp,
            )

    def nights(self, mac_id: str) -> list[sqlite3.Row]:
        return self.rows("select * from mac_nights where mac_id = ? order by date", mac_id)


def _phase(pool_id: str) -> float:
    """A stable number in [0, 1) per pool, for its emblem."""
    return (uuid.uuid5(uuid.NAMESPACE_URL, pool_id).int % 1000) / 1000


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def expires_in(days: int) -> str:
    return timefmt.iso(timefmt.now() + timedelta(days=days))


def is_expired(text: str, at: datetime | None = None) -> bool:
    return timefmt.parse(text) <= (at or timefmt.now())

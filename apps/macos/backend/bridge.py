"""Local bridge between the dots macOS app and the dots pool.

This process is a *sidecar*: the Tauri shell spawns it and talks to it over
**stdio** using newline-delimited JSON (one JSON object per line) — the same
shape LSP/DAP servers use. No TCP port, no socket file; the OS tears the child
down with the parent.

It:
  * connects a dots chat client to the hub (baked NodeId + DOTS_JOIN_TOKEN),
  * spawns a local compute worker so this machine contributes (and so a solo
    user has a pool to query),
  * answers requests on stdin and streams chat tokens back on stdout.

Protocol
--------
Requests  (shell → here), one per line on stdin:
    {"id": 1, "method": "status"}  # also: account | usage | pool | nodes | config
    {"id": 2, "method": "lend_get"}
    {"id": 3, "method": "lend_set", "params": {"gb": 24, "enabled": true, ...}}
    {"method": "chat", "prompt": "...", "max_tokens": 256, "context": {...}}

Responses (here → shell), one per line on stdout:
    {"id": 1, "result": {...}}               # reply to an id'd request
    {"id": 1, "error": "..."}
    {"chat": "token",  "text": "..."}        # chat stream
    {"chat": "status", "state": "..."}
    {"chat": "done",   "text": "...", "decode_tokens": N, "model_id": "..."}
    {"chat": "error",  "error": "..."}
    {"event": "lend", "state": {...}}        # unsolicited: lending state changed

Anything that isn't protocol (logs, tracebacks) goes to **stderr** so stdout
stays a clean JSON stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import tomllib
import urllib.request
import uuid
from collections import deque

from dots.system.util import (
    load_dotenv_into_env,
    load_account_alias,
    load_account_email,
    load_account_name,
    load_or_create_account,
)
from dots.cli import DEFAULT_HUB
from dots.distributed.worker import PoolNotReadyError, Worker

# Single source of truth for non-secret defaults — never hardcode these in
# code. Edit apps/macos/config.toml; env vars override per-run; secrets (tokens)
# stay in .env.
_CONFIG_PATH = pathlib.Path(__file__).resolve().parent.parent / "config.toml"


def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[bridge] could not read {_CONFIG_PATH}: {e}", file=sys.stderr)
        return {}


_CONFIG = _load_config()


def _conf(key: str, env: str) -> str:
    """Resolve a setting: env var (per-run override) → config.toml. No literals."""
    val = os.environ.get(env) or _CONFIG.get(key)
    if not val:
        raise RuntimeError(f"missing config '{key}' — set it in config.toml or ${env}")
    return val


def _conf_bool(key: str, env: str) -> bool:
    raw = os.environ.get(env)
    if raw is not None:
        return raw.lower() in ("1", "true", "yes")
    return bool(_CONFIG.get(key, False))


def _conf_num(key: str, env: str) -> float:
    return float(_conf(key, env))


MODEL = _conf("model", "DOTS_UI_MODEL")


def _short_model(model_id: str) -> str:
    """'mlx-community/Qwen2.5-0.5B-Instruct-4bit' -> 'Qwen2.5-0.5B'."""
    tail = model_id.split("/")[-1]
    return re.sub(r"-(4bit|8bit|Instruct)", "", tail)


def _hub_get(path: str) -> dict:
    token = os.environ.get("DOTS_JOIN_TOKEN", "")
    req = urllib.request.Request(
        _conf("hub_http", "DOTS_HUB_HTTP") + path,
        headers={
            "Authorization": f"Bearer {token}",
            "ngrok-skip-browser-warning": "true",
        },
    )
    with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


async def pool_nodes() -> dict:
    """Pool workers with location + a contribution ratio for the map.

    Keyed on account_id (alias is display-only, not unique). The ratio uses
    **lifetime** totals: earned / (earned + spent). None only when the account
    has no activity at all (brand new) so the ring shows neutral.

    Until the hub advertises per-node account_id, a node falls back to *this*
    user's account — correct for the common solo case and once deployed.
    """
    pn = await asyncio.to_thread(_hub_get, "/v1/pool/nodes")
    try:
        cr = await asyncio.to_thread(_hub_get, "/v1/credits")
    except Exception:
        cr = {"accounts": {}}

    accounts = cr.get("accounts") or {}  # keyed by account_id

    nodes = []
    for n in pn.get("nodes", []):
        geo = n.get("geo") or {}
        acc_id = n.get("account_id") or bridge.account_id
        acc = accounts.get(acc_id) or {}
        earned = float(acc.get("earned_cu") or 0)
        spent = float(acc.get("spent_cu") or 0)

        total = earned + spent
        ratio = (earned / total) if total > 0 else None  # None → no activity

        nodes.append(
            {
                "id": n.get("id"),
                "account_id": acc_id,
                "alias": n.get("alias"),
                "ready": n.get("ready"),
                "backend": n.get("backend"),
                "device": n.get("device"),  # populated once workers advertise it
                "lat": geo.get("lat"),
                "lng": geo.get("lng"),
                "city": geo.get("city"),
                "country": geo.get("country"),
                "earned_cu": round(earned, 6),  # lifetime
                "spent_cu": round(spent, 6),
                "ratio": ratio,
            }
        )
    return {"nodes": nodes}

# Most recent measured decode speed, shown on the pool.
last_tps: dict = {"value": None}

# Single writer for the stdout protocol stream. Called only from the event
# loop, so writes never interleave.
_out_lock = asyncio.Lock()


def log(*args: object) -> None:
    print("[bridge]", *args, file=sys.stderr, flush=True)


async def emit(obj: dict) -> None:
    async with _out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


class Bridge:
    """Owns the chat client + the local compute worker subprocess."""

    def __init__(self) -> None:
        self.chat: Worker | None = None
        self.account_id = load_or_create_account()
        self.ready = False
        self.error: str | None = None

    async def start(self) -> None:
        # Test/escape hatch: come up answering stdin without touching the
        # network or spawning a worker.
        if os.environ.get("DOTS_BRIDGE_NO_CONNECT"):
            log("DOTS_BRIDGE_NO_CONNECT set — not connecting")
            return
        load_dotenv_into_env()  # picks up DOTS_JOIN_TOKEN from .env
        # Lending (the local worker) is owned by `lender`, driven by the menu bar
        # dropdown's settings; it starts/stops/resizes the worker as they change.
        asyncio.create_task(lender.run())
        # Chat client: DISTINCT identity (so it doesn't take over the worker)
        # but the SAME account, so the user's earned (worker) and spent (chat)
        # CU land on one account.
        self.chat = Worker(
            DEFAULT_HUB,
            compute=False,
            requested_model_id=MODEL,
            identity=str(uuid.uuid4()),
            account_id=self.account_id,
        )
        try:
            await self.chat.connect()
            self.ready = True
            log("connected to hub; pool ready to query")
        except Exception as e:  # surfaced to the UI via status
            self.error = str(e)
            log("connect failed:", e)

    async def usage(self) -> dict:
        """This account's ledger entry (the hub returns the whole ledger)."""
        resp = await self.chat.conn.call("credits")
        ledger = resp.get("data", {})
        acc = (ledger.get("accounts") or {}).get(self.account_id) or {}
        return {
            "balance_cu": float(acc.get("balance_cu") or 0),
            "earned_cu": float(acc.get("earned_cu") or 0),
            "spent_cu": float(acc.get("spent_cu") or 0),
            "turns_as_worker": int(acc.get("turns_as_worker") or 0),
        }

    async def pool(self) -> dict:
        return await self.chat.get_pool_status(MODEL)

    async def stop(self) -> None:
        with contextlib.suppress(Exception):
            if self.chat is not None:
                await self.chat._disconnect()
        lender.stop()



# ───────────────────────── lending controller ─────────────────────────
# The menu bar dropdown drives this: how much memory to lend, when to lend
# (plugged in / not while the Mac is in use), and a one-hour pause. The local
# worker subprocess is started/stopped/resized to match.

_DOTS_DIR = pathlib.Path.home() / ".dots"
_LEND_PATH = _DOTS_DIR / "lend.json"
_LEND_DEFAULTS = {
    "enabled": True,
    "gb": None,  # None → half of the lendable memory
    "plugged_only": True,
    "pause_in_use": False,
    "paused_until": 0.0,
}


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except Exception:  # noqa: BLE001
        return ""


def read_device() -> dict:
    """Marketing name + memory of this Mac, e.g. {"name": "MacBook Pro", "gb": 48}."""
    name, gb = "Mac", 0
    try:
        hw = json.loads(_run(["system_profiler", "SPHardwareDataType", "-json"]))
        h = hw["SPHardwareDataType"][0]
        name = h.get("machine_name") or name
        m = re.match(r"(\d+)", h.get("physical_memory", ""))
        gb = int(m.group(1)) if m else 0
    except Exception:  # noqa: BLE001
        pass
    if not gb:
        try:
            gb = round(int(_run(["sysctl", "-n", "hw.memsize"]).strip()) / 2**30)
        except ValueError:
            gb = 8
    return {"name": name, "gb": gb}


def on_ac_power() -> bool:
    return "AC Power" in _run(["pmset", "-g", "batt"])


def idle_seconds() -> float:
    m = re.search(r'"HIDIdleTime" = (\d+)', _run(["ioreg", "-c", "IOHIDSystem"]))
    return int(m.group(1)) / 1e9 if m else 1e9


class Lender:
    """Policy loop + persisted settings for lending this Mac to the pool."""

    def __init__(self) -> None:
        self.device = read_device()
        self.cfg = dict(_LEND_DEFAULTS)
        try:
            self.cfg.update(json.loads(_LEND_PATH.read_text()))
        except Exception:  # noqa: BLE001
            pass
        self.proc: subprocess.Popen | None = None
        self.running_gb: int | None = None
        self.reason = "off"
        self._last: dict | None = None
        self._samples: deque[tuple[float, float]] = deque()  # (time, earned_cu)

    # memory math -----------------------------------------------------------
    @property
    def reserve_gb(self) -> int:
        return round(self.device["gb"] * _conf_num("reserve_fraction", "DOTS_RESERVE_FRACTION"))

    @property
    def max_gb(self) -> int:
        return max(1, self.device["gb"] - self.reserve_gb)

    @property
    def gb(self) -> int:
        want = self.cfg["gb"]
        if want is None:
            want = self.max_gb // 2
        return int(min(max(1, want), self.max_gb))

    # state -----------------------------------------------------------------
    def snapshot(self) -> dict:
        paused = float(self.cfg["paused_until"]) > time.time()
        return {
            "device": self.device,
            "enabled": bool(self.cfg["enabled"]),
            "gb": self.gb,
            "max_gb": self.max_gb,
            "reserve_gb": self.reserve_gb,
            "plugged_only": bool(self.cfg["plugged_only"]),
            "pause_in_use": bool(self.cfg["pause_in_use"]),
            "paused": paused,
            "lending": self.proc is not None and self.proc.poll() is None,
            "reason": self.reason,
        }

    def _save(self) -> None:
        _DOTS_DIR.mkdir(parents=True, exist_ok=True)
        _LEND_PATH.write_text(json.dumps(self.cfg))

    async def set(self, patch: dict) -> dict:
        for k in ("enabled", "plugged_only", "pause_in_use"):
            if k in patch:
                self.cfg[k] = bool(patch[k])
        if "gb" in patch and patch["gb"] is not None:
            self.cfg["gb"] = int(min(max(1, int(patch["gb"])), self.max_gb))
        if "pause_minutes" in patch:
            mins = float(patch["pause_minutes"] or 0)
            self.cfg["paused_until"] = time.time() + mins * 60 if mins > 0 else 0.0
        self._save()
        await self.tick()
        return self.snapshot()

    def _decide(self) -> tuple[bool, str]:
        if not self.cfg["enabled"]:
            return False, "off"
        if float(self.cfg["paused_until"]) > time.time():
            return False, "paused"
        if self.cfg["plugged_only"] and not on_ac_power():
            return False, "unplugged"
        quiet = _conf_num("idle_seconds", "DOTS_IDLE_SECONDS")
        if self.cfg["pause_in_use"] and idle_seconds() < quiet:
            return False, "in_use"
        return True, "lending"

    def _stop(self) -> None:
        if self.proc is not None:
            with contextlib.suppress(Exception):
                self.proc.terminate()
        self.proc, self.running_gb = None, None

    def _start(self, gb: int) -> None:
        self._stop()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "dots.cli", "worker", "--max-ram", str(gb)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ},
        )
        self.running_gb = gb

    async def tick(self) -> None:
        want, self.reason = await asyncio.to_thread(self._decide)
        if os.environ.get("DOTS_BRIDGE_NO_CONNECT"):
            want = False  # protocol-test escape hatch: never spawn a worker
        if want:
            died = self.proc is not None and self.proc.poll() is not None
            if self.proc is None or died or self.running_gb != self.gb:
                await asyncio.to_thread(self._start, self.gb)
        elif self.proc is not None:
            await asyncio.to_thread(self._stop)
        snap = self.snapshot()
        if snap != self._last:
            self._last = snap
            await emit({"event": "lend", "state": snap})

    async def run(self) -> None:
        interval = _conf_num("lend_poll_seconds", "DOTS_LEND_POLL_SECONDS")
        while True:
            with contextlib.suppress(Exception):
                await self.tick()
            await asyncio.sleep(interval)

    def earn_rate(self, earned_cu: float) -> float | None:
        """CU earned per hour over the last hour; None until there is enough history."""
        now = time.time()
        self._samples.append((now, earned_cu))
        while self._samples and now - self._samples[0][0] > 3600:
            self._samples.popleft()
        t0, e0 = self._samples[0]
        dt = now - t0
        return (earned_cu - e0) / dt * 3600 if dt >= 120 else None

    def stop(self) -> None:
        self._stop()


lender = Lender()


bridge = Bridge()




async def catalog() -> dict:
    """Everything the UI renders: real hub data first, then (if enabled) the
    preview fixture so the full design is populated before the hub serves
    pools, endorsements, badges and per-key API access."""
    try:
        nodes = (await pool_nodes())["nodes"]
    except Exception as e:  # noqa: BLE001 - hub unreachable: still serve the rest
        log("catalog: hub nodes unavailable:", e)
        nodes = []
    lenders = [
        {
            "name": n.get("alias") or (n.get("id") or "?")[:8],
            "ready": bool(n.get("ready")),
            "mine": n.get("account_id") == bridge.account_id,
        }
        for n in nodes
    ]
    mine = any(x["mine"] for x in lenders)
    real_pool = {
        "id": "hub",
        "name": _conf("pool_name", "DOTS_POOL_NAME"),
        "model": _short_model(MODEL),
        "macs": len([x for x in lenders if not x["mine"]]),
        "blurb": "The pool this hub serves.",
        "lending": len([x for x in lenders if x["ready"] and not x["mine"]]),
        "tps": last_tps["value"] or 0,
        "earn": 0,
        "friends": [],
        "endorsements": 0,
        "endorsements_label": "0",
        "tags": [],
        "public": True,
        "capacity_gb": 0,
        "lenders": [x["name"][:1].upper() for x in lenders if not x["mine"]],
        "can_run": [{"name": _short_model(MODEL), "at_gb": 0}],
    }
    token = os.environ.get("DOTS_JOIN_TOKEN", "")
    data: dict = {
        "macs_lending_now": len(lenders),
        "filters": ["All"],
        "models": _CONFIG.get("models", []),
        "joined_default": ["hub"] if mine else [],
        "pools": [real_pool],
        "badges": [],
        "profile": {"questions_helped": 0, "since": ""},
        "key_scopes": [],
        "default_monthly_limit": 0,
        "keys_default": (
            [{"id": "join", "name": "Join token", "scope": "Your pool", "tail": token[-4:],
              "usage": "", "meta": ""}] if token else []
        ),
        "request_split": [],
        "preview": False,
    }
    if _conf_bool("preview_data", "DOTS_PREVIEW_DATA"):
        fixture = json.loads((pathlib.Path(__file__).with_name("preview.json")).read_text())
        fixture.pop("_note", None)
        fixture["pools"] = [real_pool] + fixture["pools"]
        fixture["macs_lending_now"] += len(lenders)
        fixture["joined_default"] = data["joined_default"] + fixture["joined_default"]
        fixture["keys_default"] = data["keys_default"] + fixture["keys_default"]
        fixture["preview"] = True
        data = fixture
    return data



def _frontier_call(prompt: str, context: dict | None) -> dict:
    """One paid call to a frontier model, only when the user clicks Ask Claude."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    text = prompt
    if context and context.get("text"):
        text = f"{context.get('name', 'file')}:\n{context['text']}\n\n{prompt}"
    body = json.dumps(
        {
            "model": _conf("frontier_model", "DOTS_FRONTIER_MODEL"),
            "max_tokens": int(_conf_num("frontier_max_tokens", "DOTS_FRONTIER_MAX_TOKENS")),
            "messages": [{"role": "user", "content": text}],
        }
    ).encode()
    req = urllib.request.Request(
        _conf("frontier_url", "DOTS_FRONTIER_URL"),
        data=body,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310
        out = json.loads(r.read().decode("utf-8"))
    usage = out.get("usage") or {}
    return {
        "text": "".join(b.get("text", "") for b in out.get("content", [])),
        "label": _conf("frontier_label", "DOTS_FRONTIER_LABEL"),
        "tokens": int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0)),
    }


async def build_receipt(private: bool, model_id: str | None, tps: float | None) -> dict:
    """Which Macs answered, and why it ran there. Only states what is known."""
    try:
        nodes = (await pool_nodes())["nodes"]
    except Exception:  # noqa: BLE001
        nodes = []
    macs = [
        {"name": n.get("alias") or (n.get("id") or "?")[:8], "ready": bool(n.get("ready"))}
        for n in nodes
    ]
    pool = _conf("pool_name", "DOTS_POOL_NAME")
    reasons = []
    if private:
        reasons.append(
            {"icon": "lock", "text": "It includes a personal document, so it stayed in pools you're in."}
        )
    reasons.append({"icon": "dots", "text": f"You're in {pool}, so it ran there free."})
    return {
        "pool": pool,
        "model_id": model_id,
        "tps": tps,
        "macs": macs,
        "private": private,
        "free": True,
        "reasons": reasons,
    }


async def handle_chat(prompt: str, max_tokens: int, context: dict | None = None) -> None:
    if not bridge.ready or bridge.chat is None:
        await emit({"chat": "status", "state": "connecting"})
        return

    # An attached file (dropped on the Ask panel) is sent as context and keeps
    # the question in the pool: it never leaves pools the user is in.
    attached = (context or {}).get("text")
    full_prompt = (
        f"{(context or {}).get('name', 'file')}:\n{attached}\n\n{prompt}" if attached else prompt
    )

    # send_prompt's on_token is sync; hand tokens to the loop via a queue so
    # they're emitted in order from this coroutine.
    q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    timing = {"first": None}  # wall-clock of the first decoded token

    def on_tok(t: str) -> None:
        if timing["first"] is None:
            timing["first"] = time.monotonic()
        loop.call_soon_threadsafe(q.put_nowait, ("token", t))

    async def run() -> None:
        try:
            text, stats = await bridge.chat.send_prompt(
                full_prompt, max_tokens=max_tokens, on_token=on_tok
            )
            await q.put(("done", text, stats))
        except PoolNotReadyError as e:
            await q.put(("status", e.state))
        except Exception as e:  # noqa: BLE001
            await q.put(("error", str(e)))

    task = asyncio.create_task(run())
    while True:
        item = await q.get()
        if item[0] == "token":
            await emit({"chat": "token", "text": item[1]})
        elif item[0] == "done":
            stats = item[2]
            decode = stats.get("decode_tokens") or 0
            # tok/s over the decode phase (first token → now); needs >1 token.
            tps = None
            if timing["first"] and decode > 1:
                elapsed = time.monotonic() - timing["first"]
                if elapsed > 0:
                    tps = round(decode / elapsed, 1)
            if tps:
                last_tps["value"] = tps
            receipt = await build_receipt(bool(attached), stats.get("model_id"), tps)
            await emit(
                {
                    "chat": "done",
                    "text": item[1],
                    "decode_tokens": decode,
                    "model_id": stats.get("model_id"),
                    "tps": tps,
                    "receipt": receipt,
                }
            )
            break
        elif item[0] == "status":
            await emit({"chat": "status", "state": item[1]})
            break
        else:
            await emit({"chat": "error", "error": item[1]})
            break
    await task


async def dispatch(req: dict) -> None:
    method = req.get("method")
    rid = req.get("id")

    if method == "chat":
        await handle_chat(
            (req.get("prompt") or "").strip(),
            int(req.get("max_tokens", 256)),
            req.get("context") or None,
        )
        return

    # Request/response methods carry an id.
    try:
        if method == "status":
            data = {"ready": bridge.ready, "error": bridge.error, "model": MODEL}
        elif method == "account":
            data = {
                "account_id": bridge.account_id,
                "alias": load_account_alias(),
                "name": load_account_name(),
                "email": load_account_email(),
                "model": MODEL,
            }
        elif method == "usage":
            if not bridge.ready:
                raise RuntimeError("connecting")
            data = await bridge.usage()
            data = {
                **data,
                "credits_per_cu": _conf_num("credits_per_cu", "DOTS_CREDITS_PER_CU"),
                "earn_cu_per_hour": lender.earn_rate(float(data.get("earned_cu") or 0)),
            }
        elif method == "pool":
            if not bridge.ready:
                raise RuntimeError("connecting")
            data = await bridge.pool()
        elif method == "lend_get":
            data = lender.snapshot()
        elif method == "lend_set":
            data = await lender.set(req.get("params") or {})
        elif method == "config":
            data = {
                "api_base": _conf("hub_http", "DOTS_HUB_HTTP").rstrip("/") + "/v1",
                "pool_name": _conf("pool_name", "DOTS_POOL_NAME"),
                "credits_per_cu": _conf_num("credits_per_cu", "DOTS_CREDITS_PER_CU"),
                "frontier": {
                    "label": _conf("frontier_label", "DOTS_FRONTIER_LABEL"),
                    "enabled": bool(os.environ.get("ANTHROPIC_API_KEY")),
                },
            }
        elif method == "frontier":
            p = req.get("params") or {}
            data = await asyncio.to_thread(_frontier_call, p.get("prompt", ""), p.get("context"))
        elif method == "catalog":
            data = await catalog()
        elif method == "api_key":
            data = {"key": os.environ.get("DOTS_JOIN_TOKEN", "")}
        elif method == "nodes":
            data = await pool_nodes()  # hub HTTP; works even before chat connects
        else:
            raise RuntimeError(f"unknown method {method!r}")
        await emit({"id": rid, "result": data})
    except Exception as e:  # noqa: BLE001
        await emit({"id": rid, "error": str(e)})


async def main() -> None:
    # Load .env (DOTS_JOIN_TOKEN etc.) up front so HTTP calls like /nodes work
    # even before the background hub connect finishes.
    load_dotenv_into_env()
    # Connect to the hub in the background so we answer stdin immediately; the
    # UI polls "status" until ready.
    asyncio.create_task(bridge.start())

    reader = asyncio.StreamReader()
    await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
    )

    try:
        while True:
            line = await reader.readline()
            if not line:  # stdin closed → parent gone
                break
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                log("bad request line:", line[:120])
                continue
            # Each request runs concurrently so a long chat doesn't block status.
            asyncio.create_task(dispatch(req))
    finally:
        await bridge.stop()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())

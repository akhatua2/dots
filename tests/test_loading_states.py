"""Tests for the loading / waiting state UX.

The chat REPL itself uses prompt_toolkit and is hard to test headlessly,
but the protocol it depends on is:

  • Hub returns `kind: "not_ready", state: "loading" | "waiting"` when
    the pool isn't ready to serve.
  • Worker.send_prompt translates that into `PoolNotReadyError(state)`.
  • Chat catches the exception, shows a friendly inline status, and
    auto-retries.

We test the first two layers here. The chat layer is exercised
end-to-end manually via `dots chat`.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from dots.hub import Hub
from dots.peer import PeerConnection
from dots.scheduler import ResourceDecl
from dots.work_units import ModelGeometry
from dots.worker import PoolNotReadyError, Worker

from .conftest import connect_worker, wait_for_pool_size


pytestmark = pytest.mark.asyncio


# ───────────────────── hub-side state classification ─────────────────────


def test_not_ready_state_is_waiting_when_no_placement():
    """No placement decided yet (zero compute workers, or pool too small
    to fit the model) → state=waiting. The chat surface translates this
    to 'Waiting for more machines…'."""
    hub = Hub(port=0, model_id="fake/x")
    hub._geometry = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    hub.n_layers = 4
    # No participants, no _current_placement.
    resp = hub._not_ready_status_response()
    assert resp == {"kind": "not_ready", "state": "waiting"}


def test_not_ready_state_is_loading_when_placement_set():
    """A placement was computed (workers know what to load) but they
    haven't reported back yet → state=loading. The chat surface shows
    'Loading the model…'."""
    hub = Hub(port=0, model_id="fake/x")
    hub._geometry = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    hub.n_layers = 4
    hub._current_placement = [("W1", 0, 4, True)]
    resp = hub._not_ready_status_response()
    assert resp == {"kind": "not_ready", "state": "loading"}


def test_not_ready_response_never_leaks_technical_details():
    """The response carries `state` only — no RAM numbers, no model id,
    no worker counts. The chat UI must show the user-facing message,
    never a serialized snippet."""
    hub = Hub(port=0, model_id="fake/x")
    hub._geometry = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    hub.n_layers = 4
    waiting = hub._not_ready_status_response()
    hub._current_placement = [("W1", 0, 4, True)]
    loading = hub._not_ready_status_response()
    for resp in (waiting, loading):
        for k in resp:
            # Allowed keys only — anything else is a leak.
            assert k in {"kind", "state"}, f"leaking field {k}: {resp}"


# ──────────────── worker-side translation to PoolNotReadyError ───────────


class _FakePromptConn:
    """A minimal PeerConnection stand-in that returns whatever response
    we configure on each `call`. Used to test how Worker.send_prompt
    interprets hub replies."""

    def __init__(self, response: dict):
        self.response = response

    async def call(self, _kind: str, **_kw):
        return self.response


async def test_send_prompt_raises_pool_not_ready_for_waiting_state():
    w = Worker("hub-host", 1, identity="w0", account_id="acc-w0",
               account_alias="")
    w.conn = _FakePromptConn({"kind": "not_ready", "state": "waiting"})
    with pytest.raises(PoolNotReadyError) as exc_info:
        await w.send_prompt("hi")
    assert exc_info.value.state == "waiting"


async def test_send_prompt_raises_pool_not_ready_for_loading_state():
    w = Worker("hub-host", 1, identity="w0", account_id="acc-w0",
               account_alias="")
    w.conn = _FakePromptConn({"kind": "not_ready", "state": "loading"})
    with pytest.raises(PoolNotReadyError) as exc_info:
        await w.send_prompt("hi")
    assert exc_info.value.state == "loading"


async def test_send_prompt_normal_path_still_works():
    """Sanity: a real turn_done response still returns text + stats."""
    w = Worker("hub-host", 1, identity="w0", account_id="acc-w0",
               account_alias="")
    w.conn = _FakePromptConn({
        "kind": "turn_done", "prefill_tokens": 5, "decode_tokens": 3,
    })
    text, resp = await w.send_prompt("hi")
    assert resp["decode_tokens"] == 3
    assert text == ""  # no token notifications fired in the stub


# ─────────────────── end-to-end against the hub fixture ──────────────────


async def test_chat_client_gets_waiting_when_no_compute_workers(hub: Hub):
    """A chat client connects, no compute worker has joined → sending a
    prompt yields PoolNotReadyError(waiting) on the worker side."""
    # Connect a chat-only client by hand (the standard `connect_worker`
    # fixture is for compute workers).
    reader, writer = await asyncio.open_connection("127.0.0.1", hub.port)
    cid = str(uuid.uuid4())
    chat = Worker(
        "127.0.0.1", hub.port,
        identity=cid,
        account_id="acc-" + cid[:8],
        account_alias="",
        compute=False,
    )
    chat.conn = PeerConnection(
        reader, writer, f"127.0.0.1:{hub.port}", chat._handle,
    )
    await chat.conn.call(
        "hello", worker_id=cid, account_id=chat.account_id,
        resources={"ram_gb_free": 4.0, "compute_tflops_per_sec": 0.0,
                   "backends": [], "cluster_id": ""},
        compute=False,
    )
    await asyncio.wait_for(chat._welcome.wait(), timeout=2.0)
    try:
        with pytest.raises(PoolNotReadyError) as exc_info:
            await chat.send_prompt("hi")
        # No compute workers → no placement → waiting.
        assert exc_info.value.state == "waiting"
    finally:
        await chat.conn.close()

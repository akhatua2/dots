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
from dots.pool import resource_decl_to_hello_field
from dots.work_units import ModelGeometry
from dots.worker import PoolNotReadyError, Worker
from tests.conftest import TEST_MODEL
from tests.helpers.resources import resource_decl


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


# ──────────── status routing: per-machine progress + fallback ────────────


def _seed_hub_with_two_workers() -> Hub:
    """Build a hub with two workers in placement, both contributing to
    the same model load, but each owned by a different account. Used
    to test the per-machine routing of the status endpoint.
    """
    from dots.hub import Participant

    hub = Hub(port=0, model_id="fake/x")
    hub._geometry = ModelGeometry(n_layers=4, hidden_size=128, vocab_size=1000)
    hub.n_layers = 4

    # Two workers + one chat client. Each worker has its own account_id;
    # the chat client matches one of them (its companion).
    class _StubConn:
        async def call(self, *a, **k):
            return {}

        async def notify(self, *a, **k):
            return None

        async def close(self):
            return None

    fast_worker = Participant(
        id="W-fast",
        addr="127.0.0.1:1",
        conn=_StubConn(),
        compute=True,
        account_id="acc-arpan",
    )
    slow_worker = Participant(
        id="W-slow",
        addr="100.77.18.19:1",
        conn=_StubConn(),
        compute=True,
        account_id="acc-arpan-nvidia",
    )
    chat_local = Participant(
        id="C-arpan",
        addr="127.0.0.1:2",
        conn=_StubConn(),
        compute=False,
        account_id="acc-arpan",
    )
    hub.participants[fast_worker.id] = fast_worker
    hub.participants[slow_worker.id] = slow_worker
    hub.participants[chat_local.id] = chat_local
    hub._current_placement = [
        ("W-fast", 0, 2, True),
        ("W-slow", 2, 4, False),
    ]
    # fast: 90% done. slow: 1% done.
    hub._load_progress["W-fast"] = (9_000, 10_000)
    hub._load_progress["W-slow"] = (100, 10_000)
    return hub


def test_status_routes_to_local_worker_by_account_id():
    """A chat client's status response carries ITS own machine's worker
    progress — not the slowest pool-wide. Two chats on two different
    machines must see DIFFERENT progress numbers."""
    hub = _seed_hub_with_two_workers()
    resp = hub._status_response_for("C-arpan")
    assert resp["ready"] is False
    assert resp["state"] == "loading"
    assert resp["bytes_now"] == 9_000, (
        f"chat should see its companion fast_worker's bytes; got {resp}"
    )
    assert resp["progress"] == pytest.approx(0.9)


def test_status_for_chat_only_client_shows_no_bar():
    """Chat-only clients (no companion worker on the same account) aren't
    downloading anything themselves. Showing them a bar of *someone
    else's* progress would be misleading. They get the friendly
    'Waiting for other machines' text with no bar."""
    hub = _seed_hub_with_two_workers()
    from dots.hub import Participant

    class _StubConn:
        async def call(self, *a, **k):
            return {}

        async def notify(self, *a, **k):
            return None

        async def close(self):
            return None

    hub.participants["C-orphan"] = Participant(
        id="C-orphan",
        addr="10.0.0.5:1",
        conn=_StubConn(),
        compute=False,
        account_id="acc-other",
    )
    resp = hub._status_response_for("C-orphan")
    assert resp["ready"] is False
    assert resp["state"] == "loading_peers"
    assert "progress" not in resp, (
        f"chat-only clients shouldn't see a borrowed bar; got {resp}"
    )


def test_status_switches_to_waiting_for_peers_when_local_done():
    """Once the local worker has finished downloading but the pool isn't
    ready (a peer is still loading), the chat must NOT keep displaying
    the bar — it'd freeze at 100% and look stuck. Switch state to
    `loading_peers` so the chat shows the friendly 'Waiting for other
    machines' text without a bar."""
    hub = _seed_hub_with_two_workers()
    hub._load_progress["W-fast"] = (10_000, 10_000)  # local done
    resp = hub._status_response_for("C-arpan")
    assert resp["state"] == "loading_peers", (
        f"state should switch to loading_peers when local is done; got {resp}"
    )
    assert "progress" not in resp, (
        f"no bar should be sent once local is done; got {resp}"
    )


def test_pool_ready_requires_ALL_workers_in_placement():
    """Multi-worker chains break if pool_ready fires before every
    assigned worker reports back: arpan finishes its 1 layer + head,
    pool flips ready, chat UI opens, first prompt hits arpan, arpan
    forwards hidden state to arpan-nvidia — which is still loading
    63 layers — and the chain hangs forever.

    The previous version used `any(p.ready)` here; pinning the fix to
    `all(p.ready for p in placement_workers)` so the regression can't
    return."""
    hub = _seed_hub_with_two_workers()
    hub._pool_ready.clear()
    # fast: ready. slow: still loading.
    hub.participants["W-fast"].ready = True
    hub.participants["W-slow"].ready = False
    hub._refresh_pool_ready()
    assert not hub._pool_ready.is_set(), (
        "pool must NOT be ready while a placement worker is still loading"
    )
    # Once the slow one finishes loading too:
    hub.participants["W-slow"].ready = True
    hub._refresh_pool_ready()
    assert hub._pool_ready.is_set(), (
        "pool should become ready when every placement worker is ready"
    )


def test_pool_ready_clears_when_no_placement():
    """No placement → no pool. Trivially: a hub with no workers in
    its current placement can never be 'ready'."""
    hub = _seed_hub_with_two_workers()
    hub._current_placement = []
    hub._pool_ready.set()
    hub._refresh_pool_ready()
    assert not hub._pool_ready.is_set()


def test_status_returns_ready_when_pool_ready():
    """Once `_pool_ready` is set, the chat client gets the minimal
    `ready: True` response and dismisses the spinner immediately."""
    hub = _seed_hub_with_two_workers()
    hub._pool_ready.set()
    resp = hub._status_response_for("C-arpan")
    assert resp == {"kind": "status", "ready": True}


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
    w = Worker("hub-host", 1, identity="w0", account_id="acc-w0", account_alias="")
    w.conn = _FakePromptConn({"kind": "not_ready", "state": "waiting"})
    with pytest.raises(PoolNotReadyError) as exc_info:
        await w.send_prompt("hi")
    assert exc_info.value.state == "waiting"


async def test_send_prompt_raises_pool_not_ready_for_loading_state():
    w = Worker("hub-host", 1, identity="w0", account_id="acc-w0", account_alias="")
    w.conn = _FakePromptConn({"kind": "not_ready", "state": "loading"})
    with pytest.raises(PoolNotReadyError) as exc_info:
        await w.send_prompt("hi")
    assert exc_info.value.state == "loading"


async def test_send_prompt_normal_path_still_works():
    """Sanity: a real turn_done response still returns text + stats."""
    w = Worker("hub-host", 1, identity="w0", account_id="acc-w0", account_alias="")
    w.conn = _FakePromptConn(
        {
            "kind": "turn_done",
            "prefill_tokens": 5,
            "decode_tokens": 3,
        }
    )
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
        "127.0.0.1",
        hub.port,
        identity=cid,
        account_id="acc-" + cid[:8],
        account_alias="",
        compute=False,
        # The hub now requires the querier to name a model on each
        # request (it advertises none of its own).
        requested_model_id=TEST_MODEL,
    )
    chat.conn = PeerConnection(
        reader,
        writer,
        f"127.0.0.1:{hub.port}",
        chat._handle,
    )
    await chat.conn.call(
        "hello",
        worker_id=cid,
        account_id=chat.account_id,
        resources=resource_decl_to_hello_field(
            resource_decl(
                cid,
                account_id=chat.account_id,
                ram_gb=4.0,
                tflops=0.0,
                backends=frozenset(),
                cluster_id="",
            )
        ),
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

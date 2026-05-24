"""The hub's optional shared join token (DOTS_JOIN_TOKEN).

When set, every connecting worker/client must present a matching token in
HELLO — a second factor on top of knowing the hub's iroh ticket. When unset,
the pool is open to anyone with the ticket.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from dots.distributed.hub import Hub
from dots.distributed.peer import PeerConnection
from dots.distributed.protocol import InMemoryTransport
from dots.distributed.pool import resource_decl_to_hello_field
from tests.helpers.resources import resource_decl

pytestmark = pytest.mark.asyncio


async def _noop(_msg: dict) -> None:
    return None


async def _say_hello(hub: Hub, token: str | None) -> dict:
    """Drive one HELLO against the hub over an in-memory transport and return
    the ack (or error)."""
    hub_side, worker_side = InMemoryTransport.pair()
    serve = asyncio.create_task(hub._serve_peer(hub_side, "peer"))
    conn = PeerConnection(worker_side, "hub", _noop)
    cid = str(uuid.uuid4())
    payload: dict = dict(
        worker_id=cid,
        account_id="acc-" + cid[:8],
        compute=False,
        resources=resource_decl_to_hello_field(
            resource_decl(
                cid,
                account_id="acc-" + cid[:8],
                ram_gb=4.0,
                tflops=0.0,
                backends=frozenset(),
                cluster_id="",
            )
        ),
    )
    if token is not None:
        payload["join_token"] = token
    try:
        return await asyncio.wait_for(conn.call("hello", **payload), timeout=2.0)
    finally:
        await conn.close()
        serve.cancel()


async def test_wrong_token_rejected_and_right_token_accepted(monkeypatch):
    monkeypatch.setenv("DOTS_JOIN_TOKEN", "s3cret")
    hub = Hub(port=0)
    assert hub._join_token == "s3cret"

    bad = await _say_hello(hub, "wrong")
    assert bad.get("kind") == "error", bad

    good = await _say_hello(hub, "s3cret")
    assert good.get("kind") == "hello_ack", good


async def test_missing_token_rejected_when_hub_requires_one(monkeypatch):
    monkeypatch.setenv("DOTS_JOIN_TOKEN", "s3cret")
    hub = Hub(port=0)
    missing = await _say_hello(hub, None)
    assert missing.get("kind") == "error", missing


async def test_open_pool_when_no_token_configured(monkeypatch):
    monkeypatch.delenv("DOTS_JOIN_TOKEN", raising=False)
    hub = Hub(port=0)
    assert hub._join_token is None
    ack = await _say_hello(hub, None)
    assert ack.get("kind") == "hello_ack", ack

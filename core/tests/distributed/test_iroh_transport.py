"""Phase 1: the iroh transport carries PeerConnection RPC end-to-end.

Two real iroh nodes: one listens on the dots ALPN and echoes; the other dials
it by ticket and runs request/response + notify over the same channel. This
exercises IrohEndpoint, IrohStreamTransport (framing over a QUIC bi-stream),
and PeerConnection together — the whole wire stack with no TCP.
"""

import asyncio

import pytest

from dots.distributed.iroh_net import IrohEndpoint
from dots.distributed.peer import PeerConnection


async def _noop(msg: dict) -> dict:
    return {"kind": "ok"}


@pytest.mark.asyncio
async def test_iroh_rpc_round_trip_and_notify():
    notes: list[str] = []

    async def server_handler(msg: dict) -> dict | None:
        kind = msg.get("kind")
        if kind == "say":
            return {"kind": "echo", "text": msg.get("text")}
        if kind == "note":  # notification, no rid -> no reply expected
            notes.append(msg.get("text", ""))
            return None
        return {"kind": "error", "error": f"unknown {kind}"}

    server_conns: list[PeerConnection] = []

    async def on_accept(transport, peer_id):
        conn = PeerConnection(transport, peer_id, server_handler)
        server_conns.append(conn)
        await conn._closed.wait()

    # discovery=False: stay offline (connect via the ticket's direct loopback
    # addresses, no n0 DNS/relay network calls).
    server = await IrohEndpoint.create(on_accept, discovery=False)
    client = await IrohEndpoint.create(_noop, discovery=False)
    ticket = await server.ticket()

    client_conn = await asyncio.wait_for(client.connect(ticket, _noop), timeout=30)

    # request / response
    resp = await asyncio.wait_for(client_conn.call("say", text="hello-iroh"), timeout=30)
    assert resp.get("kind") == "echo"
    assert resp.get("text") == "hello-iroh"

    # fire-and-forget notify
    await client_conn.notify("note", text="ping")
    for _ in range(100):
        if notes:
            break
        await asyncio.sleep(0.05)
    assert notes == ["ping"]

    await client_conn.close()
    await client.shutdown()
    await server.shutdown()


@pytest.mark.asyncio
async def test_iroh_ticket_is_stable_with_persistent_key():
    from dots.distributed.iroh_net import load_or_create_secret_key

    # Same secret key -> same NodeId -> stable, shareable identity.
    key = load_or_create_secret_key("test-stable")
    ep1 = await IrohEndpoint.create(_noop, secret_key=key, discovery=False)
    id1 = await ep1.node_id()
    await ep1.shutdown()

    ep2 = await IrohEndpoint.create(_noop, secret_key=key, discovery=False)
    id2 = await ep2.node_id()
    await ep2.shutdown()

    assert id1 == id2

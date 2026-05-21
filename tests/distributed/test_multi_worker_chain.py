"""End-to-end test of the multi-worker pipeline.

Two workers connect; the hub builds a chain spec that splits the 8-layer
fake model across both (head: layers 0–3, chain peer: layers 4–7). A chat
client sends a prompt; tokens stream back; the worker on the head end
actually opened a peer connection to the chain worker and forwarded
hidden states through it.

This is the smoke test for the data-plane swap: workers talk directly to
each other for hidden states; the hub stays off the per-token path.
"""

from __future__ import annotations

import asyncio

import pytest

from dots.hub import Hub

from tests.conftest import connect_worker, wait_for_pool_size


pytestmark = pytest.mark.asyncio


async def test_chain_routes_through_peer_worker(hub: Hub):
    """Send delegate_turn with an explicit chain spec; the head worker
    runs its slice, forwards hidden state to the chain peer, gets it
    back, and samples + streams tokens."""
    w_head = await connect_worker(hub)
    w_chain = await connect_worker(hub)
    await wait_for_pool_size(hub, 2)

    # Build a chain placement: w_head owns layers 0-3 + embed + sample,
    # w_chain owns layers 4-7.
    n_layers = hub.n_layers
    split = n_layers // 2
    chain_payload = [
        {
            "worker_id": w_chain.identity,
            "host": "127.0.0.1",
            "port": int(w_chain._peer_listen_port),
            "layer_start": split,
            "layer_end": n_layers,
        }
    ]

    # The hub's `_token_routers` table forwards `delegate_token` to a
    # callback. We register one that collects fragments.
    fragments: list[str] = []

    async def collect(text: str) -> None:
        fragments.append(text)

    # Pick the head's conv id off its hub-side participant.
    head_p = hub.participants[w_head.identity]
    conv_id = head_p.conv.id
    hub._token_routers[conv_id] = collect

    # Drive the head worker manually with the chain spec (hub-side conn).
    resp = await head_p.conn.call(
        "delegate_turn",
        conv_id=conv_id,
        history=[{"role": "user", "content": "hi"}],
        max_tokens=3,
        temperature=0,
        my_layer_end=split,
        chain=chain_payload,
    )

    assert resp.get("kind") == "delegate_done", resp
    assert resp["text"], f"empty chain response: {resp}"
    assert resp["decode_tokens"] == 3, resp
    # Tokens were streamed (delegate_token notifications went through hub).
    assert fragments, "no fragments arrived via the hub's token router"


async def test_chain_uses_both_workers_caches(hub: Hub):
    """Two chain workers should each grow their own KV cache for the
    same conv_id — the head holds layers 0-3's cache, the peer holds 4-7."""
    w_head = await connect_worker(hub)
    w_chain = await connect_worker(hub)
    await wait_for_pool_size(hub, 2)

    n_layers = hub.n_layers
    split = n_layers // 2
    chain_payload = [
        {
            "worker_id": w_chain.identity,
            "host": "127.0.0.1",
            "port": int(w_chain._peer_listen_port),
            "layer_start": split,
            "layer_end": n_layers,
        }
    ]

    head_p = hub.participants[w_head.identity]
    conv_id = head_p.conv.id

    # Stub the on_token router (we don't care about fragments here).
    hub._token_routers[conv_id] = lambda _t: asyncio.sleep(0)

    await head_p.conn.call(
        "delegate_turn",
        conv_id=conv_id,
        history=[{"role": "user", "content": "hello"}],
        max_tokens=2,
        temperature=0,
        my_layer_end=split,
        chain=chain_payload,
    )

    # Both hosts should have a cache entry under the same conv_id.
    assert conv_id in w_head.host._caches
    assert conv_id in w_chain.host._caches
    # And both caches should have advanced.
    head_cache_lens = {c.len for c in w_head.host._caches[conv_id][:split]}
    chain_cache_lens = {c.len for c in w_chain.host._caches[conv_id][split:n_layers]}
    assert head_cache_lens and all(n > 0 for n in head_cache_lens), head_cache_lens
    assert chain_cache_lens and all(n > 0 for n in chain_cache_lens), chain_cache_lens

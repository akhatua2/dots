"""The ring: a pass goes head -> each peer -> head, with no stop back at the head in between."""

from __future__ import annotations

import asyncio

import pytest

from dots.distributed.worker import Worker

MODEL = "m"


class AddLayers:
    """A stand-in model: the hidden state is a number, and each layer adds one."""

    def __init__(self, fail: bool = False):
        self.fail = fail

    def forward_segment(self, conv_id, hidden, start, end):
        if self.fail:
            raise RuntimeError("out of memory")
        return hidden + (end - start)

    def encode_hidden(self, hidden):
        return {"value": hidden}

    def decode_hidden(self, payload):
        return payload["value"]


class Link:
    """A peer connection that delivers straight to another worker, counting the legs it carries."""

    legs = 0

    def __init__(self, to: Worker):
        self.to = to

    async def notify(self, kind, **payload):
        Link.legs += 1
        asyncio.get_running_loop().create_task(self.to._handle_peer_msg({"kind": kind, **payload}))


def pool(fail_last: bool = False) -> tuple[Worker, list[dict]]:
    """A head and two peers: the head runs layers 0-2, peer a 2-5, peer b 5-8."""
    workers = {name: Worker("hub-ticket", headless=True) for name in ("head", "a", "b")}
    for name, w in workers.items():
        w._peer_ticket = f"ticket-{name}"
        w.backends[MODEL] = AddLayers(fail=fail_last and name == "b")
        w._ready_event_for(MODEL).set()

    async def dial(_peer_id, ticket):
        return Link(workers[ticket.removeprefix("ticket-")])

    for w in workers.values():
        w._get_or_open_peer = dial
    chain = [{"worker_id": "a", "ticket": "ticket-a", "layer_start": 2, "layer_end": 5},
             {"worker_id": "b", "ticket": "ticket-b", "layer_start": 5, "layer_end": 8}]
    return workers, chain


async def test_a_pass_goes_round_the_ring_and_comes_home_once():
    workers, chain = pool()
    head = workers["head"]
    forward = head._ring_forwarder(conv_id="c", model_id=MODEL, runtime=head.backends[MODEL], my_layer_end=2,
                                   chain=chain, first=Link(workers["a"]))
    Link.legs = 0
    assert await forward(0) == 8, "every layer ran, in order"
    assert Link.legs == 3, "head to a, a to b, b home: n + 1 legs for n peers, not 2n"
    stats = forward.timing.stats()
    assert stats["chain"] == "ring" and stats["chain_passes"] == 1


async def test_a_peer_that_fails_tells_the_head_at_once():
    workers, chain = pool(fail_last=True)
    head = workers["head"]
    forward = head._ring_forwarder(conv_id="c", model_id=MODEL, runtime=head.backends[MODEL], my_layer_end=2,
                                   chain=chain, first=Link(workers["a"]))
    with pytest.raises(RuntimeError, match="out of memory"):
        await asyncio.wait_for(forward(0), timeout=2)

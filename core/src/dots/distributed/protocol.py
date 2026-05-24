"""Wire framing + the transport abstraction for peer RPC channels.

Every message is a dict serialized with msgpack. On the wire a frame is a
4-byte big-endian length prefix followed by the msgpack payload. This framing
is transport-agnostic: `PeerConnection` (see peer.py) runs all of its RPC
bookkeeping — request ids, response futures, dispatch — on top of any object
satisfying the `Transport` protocol (`send` / `recv` / `close`).

Two transports implement it:
  * `IrohStreamTransport` (iroh_net.py) — production: msgpack frames over an
    iroh bidirectional QUIC stream (NAT-traversed, relay-backed).
  * `InMemoryTransport` (here) — tests: an in-process queue pair, no sockets.

Hidden-state tensors travel as raw bytes plus shape + dtype, since msgpack's
native numpy support is patchy and we need a stable representation across
bfloat16 (which numpy doesn't natively support).
"""

from __future__ import annotations

import asyncio
import struct
from typing import Any, Protocol, runtime_checkable

import msgpack

LEN_HEADER = struct.Struct(">I")
MAX_FRAME_BYTES = 64 * 1024 * 1024  # 64 MB — generous for prefill activations


def pack_msg(msg: dict[str, Any]) -> bytes:
    """Serialize one message dict to its msgpack payload (no length prefix)."""
    return msgpack.packb(msg, use_bin_type=True)


def unpack_msg(payload: bytes) -> dict[str, Any]:
    """Deserialize a msgpack payload back to a dict."""
    return msgpack.unpackb(payload, raw=False)


@runtime_checkable
class Transport(Protocol):
    """A bidirectional message pipe carrying whole dicts.

    Contract for `recv()`: on a clean remote close it must raise one of the
    disconnect exceptions `PeerConnection._read_loop` treats as a normal
    hang-up — `ConnectionResetError`, `ConnectionAbortedError`,
    `BrokenPipeError`, or `asyncio.IncompleteReadError` — so every transport
    looks identical to the read loop.
    """

    async def send(self, msg: dict[str, Any]) -> None: ...

    async def recv(self) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class InMemoryTransport:
    """An in-process transport for tests: a linked pair of queues, no I/O.

    `InMemoryTransport.pair()` returns two transports where whatever one sends
    the other receives. Closing either side unblocks both `recv()` loops with
    a `ConnectionResetError`, mirroring a real socket hang-up.
    """

    _SENTINEL = object()

    def __init__(self, inbox: asyncio.Queue, outbox: asyncio.Queue):
        self._inbox = inbox
        self._outbox = outbox
        self._closed = False

    @classmethod
    def pair(cls) -> tuple["InMemoryTransport", "InMemoryTransport"]:
        q1: asyncio.Queue = asyncio.Queue()
        q2: asyncio.Queue = asyncio.Queue()
        return cls(q1, q2), cls(q2, q1)

    async def send(self, msg: dict[str, Any]) -> None:
        if self._closed:
            raise ConnectionResetError("transport closed")
        await self._outbox.put(msg)

    async def recv(self) -> dict[str, Any]:
        item = await self._inbox.get()
        if item is self._SENTINEL:
            raise ConnectionResetError("peer closed")
        return item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Wake the peer's recv and our own.
        await self._outbox.put(self._SENTINEL)
        await self._inbox.put(self._SENTINEL)

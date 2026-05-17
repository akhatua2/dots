"""Bidirectional RPC channel between two peers over a single TCP connection.

A peer connection multiplexes outgoing requests and incoming requests on the
same socket using a `rid` correlation id. Messages carrying `response_to`
resolve a pending Future; messages without are incoming requests and get
handed to the local Node's handler.

Connection ownership convention (avoids both sides opening a connection to
each other): the peer with the *lower* sorted self-address opens the
connection; the higher one accepts it. Both sides then use the same socket.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from .protocol import read_msg, write_msg

log = logging.getLogger("dots.peer")

RequestHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class PeerConnection:
    """One bidirectional RPC channel."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        peer_id: str,
        handler: RequestHandler,
    ):
        self.reader = reader
        self.writer = writer
        self.peer_id = peer_id
        self.handler = handler
        self._next_rid = 1  # 0 reserved for "unset"
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self._send_lock = asyncio.Lock()  # serialize frame writes
        self._read_task = asyncio.create_task(self._read_loop())
        self._closed = asyncio.Event()

    async def call(self, kind: str, **payload: Any) -> dict[str, Any]:
        """Send a request, wait for the response. Raises if connection drops."""
        rid = self._next_rid
        self._next_rid += 1
        fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        msg = {"kind": kind, "rid": rid, **payload}
        async with self._send_lock:
            await write_msg(self.writer, msg)
        try:
            return await fut
        finally:
            self._pending.pop(rid, None)

    async def notify(self, kind: str, **payload: Any) -> None:
        """Fire-and-forget message; receiver dispatches to handler but no
        response is expected or sent. Used for streaming tokens where one
        RTT per token would halve throughput."""
        msg = {"kind": kind, **payload}  # no rid
        async with self._send_lock:
            await write_msg(self.writer, msg)

    async def _read_loop(self) -> None:
        try:
            while True:
                msg = await read_msg(self.reader)
                if "response_to" in msg:
                    fut = self._pending.get(msg["response_to"])
                    if fut and not fut.done():
                        fut.set_result(msg)
                elif "rid" in msg:
                    # Incoming request — dispatch in a background task so we
                    # don't block the read loop on slow handlers.
                    asyncio.create_task(self._serve(msg))
                else:
                    # Notification — dispatch but don't reply.
                    asyncio.create_task(self._notify_handler(msg))
        except asyncio.IncompleteReadError:
            log.info("connection to %s closed", self.peer_id)
        except Exception:
            log.exception("read loop error on %s", self.peer_id)
        finally:
            self._closed.set()
            # Fail any in-flight requests so callers don't hang.
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError(
                        f"peer {self.peer_id} disconnected"))

    async def _notify_handler(self, msg: dict[str, Any]) -> None:
        try:
            await self.handler(msg)
        except Exception:
            log.exception("notification handler error for %s", msg.get("kind"))

    async def _serve(self, msg: dict[str, Any]) -> None:
        rid = msg.get("rid")
        try:
            resp = await self.handler(msg)
            if resp is None:
                resp = {"kind": "ok"}
        except Exception as e:
            log.exception("handler error for %s", msg.get("kind"))
            resp = {"kind": "error", "error": str(e)}
        resp["response_to"] = rid
        try:
            async with self._send_lock:
                await write_msg(self.writer, resp)
        except Exception:
            log.exception("failed to send response for rid=%s", rid)

    async def close(self) -> None:
        self._read_task.cancel()
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except Exception:
            pass

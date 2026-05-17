"""Worker daemon: TCP server that runs one shard of the model.

Protocol it speaks:
    HELLO            -> HELLO_ACK {free_ram_gb, n_layers_supported}
    LOAD_SHARD       -> READY | ERROR
    FORWARD          -> FORWARD_OUT
    RESET_KV         -> OK
    PING             -> PONG
"""
from __future__ import annotations

import asyncio
import logging
import subprocess

from .protocol import decode_array, encode_array, read_msg, write_msg
from .shard import Shard, ShardSpec

log = logging.getLogger("dots.worker")


def _free_ram_gb() -> float:
    # vm_stat gives free + inactive in pages; LPDDR5 page = 16384 on Apple Silicon.
    out = subprocess.check_output(["vm_stat"]).decode()
    pages_free = pages_inactive = 0
    for line in out.splitlines():
        if line.startswith("Pages free:"):
            pages_free = int(line.split()[-1].rstrip("."))
        elif line.startswith("Pages inactive:"):
            pages_inactive = int(line.split()[-1].rstrip("."))
    return (pages_free + pages_inactive) * 16384 / (1024**3)


class WorkerSession:
    """One connection from a coordinator. Owns at most one Shard."""

    def __init__(self):
        self.shard: Shard | None = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        log.info("session opened from %s", peer)
        try:
            while True:
                msg = await read_msg(reader)
                resp = await self._dispatch(msg)
                if resp is not None:
                    await write_msg(writer, resp)
        except asyncio.IncompleteReadError:
            log.info("session closed from %s", peer)
        except Exception:
            log.exception("session error from %s", peer)
        finally:
            writer.close()
            await writer.wait_closed()

    async def _dispatch(self, msg: dict) -> dict | None:
        kind = msg.get("kind")
        if kind == "hello":
            return {
                "kind": "hello_ack",
                "free_ram_gb": _free_ram_gb(),
                "version": "0.0.1",
            }
        if kind == "load_shard":
            return await self._load_shard(msg)
        if kind == "forward":
            return self._forward(msg)
        if kind == "reset_kv":
            if self.shard:
                self.shard.reset_kv()
            return {"kind": "ok"}
        if kind == "ping":
            return {"kind": "pong"}
        return {"kind": "error", "error": f"unknown kind: {kind}"}

    async def _load_shard(self, msg: dict) -> dict:
        spec = ShardSpec(
            model_id=msg["model_id"],
            layer_start=msg["layer_start"],
            layer_end=msg["layer_end"],
            is_first=msg["is_first"],
            is_last=msg["is_last"],
        )
        log.info("loading shard: %s", spec)
        # mlx_load is sync; offload to a thread so we don't block the loop.
        self.shard = await asyncio.to_thread(Shard, spec)
        log.info("shard ready (n_layers=%d)", self.shard.n_layers)
        return {"kind": "ready", "n_layers": self.shard.n_layers}

    def _forward(self, msg: dict) -> dict:
        if self.shard is None:
            return {"kind": "error", "error": "no shard loaded"}
        hidden = decode_array(msg["hidden"])
        out = self.shard.run_layers(hidden)
        return {"kind": "forward_out", "hidden": encode_array(out)}


async def serve(host: str, port: int):
    async def on_connect(r, w):
        await WorkerSession().handle(r, w)

    server = await asyncio.start_server(on_connect, host=host, port=port)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    log.info("worker listening on %s", addrs)
    async with server:
        await server.serve_forever()

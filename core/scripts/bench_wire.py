"""Round trips over the peer transport on this Mac: how much a hop costs before any network.

Two iroh endpoints on loopback, one echoing every request. A hidden state for one token of a
1536-wide model is 3 KB in bf16; a pass verifying four draft tokens carries five, 15 KB.

    uv run python scripts/bench_wire.py
"""

from __future__ import annotations

import asyncio
import statistics
import time

from dots.distributed.iroh_net import IrohEndpoint
from dots.distributed.peer import PeerConnection

SIZES = {"ping": 0, "1 token (3 KB)": 3_072, "5 tokens (15 KB)": 15_360, "64 tokens (192 KB)": 196_608}
ROUNDS = 300


async def echo(msg: dict) -> dict:
    return {"kind": "echo", "data": msg.get("data", b"")}


async def main() -> None:
    held: list[PeerConnection] = []

    async def on_accept(transport, peer_id):
        conn = PeerConnection(transport, peer_id, echo)
        held.append(conn)
        await conn._closed.wait()

    server = await IrohEndpoint.create(on_accept, discovery=False)
    client = await IrohEndpoint.create(on_accept, discovery=False)
    conn = await client.connect(await server.node_addr(), echo)

    print(f"{'payload':<20} {'median ms':>10} {'p90 ms':>8}")
    for name, size in SIZES.items():
        data = bytes(i % 251 for i in range(size))
        assert (await conn.call("echo", data=data))["data"] == data, "bytes must arrive as sent"
        for _ in range(20):  # warm up
            await conn.call("echo", data=data)
        times = []
        for _ in range(ROUNDS):
            t = time.perf_counter()
            await conn.call("echo", data=data)
            times.append((time.perf_counter() - t) * 1000)
        times.sort()
        print(f"{name:<20} {statistics.median(times):>10.3f} {times[int(len(times) * 0.9)]:>8.3f}")


if __name__ == "__main__":
    asyncio.run(main())

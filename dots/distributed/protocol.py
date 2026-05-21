"""Wire protocol: length-prefixed msgpack frames over asyncio TCP.

Every message is a dict with at least:
    kind: str          message type (e.g. "hello", "compute", "ok")
    rid:  int          request id, unique per sender per connection

Responses additionally carry:
    response_to: int   the rid of the request they answer

Hidden-state tensors travel as raw bytes plus shape + dtype, since
msgpack-native numpy support is patchy and we need a stable on-the-wire
representation across bfloat16 (which numpy doesn't natively support).
"""

from __future__ import annotations

import asyncio
import struct
from typing import Any

import msgpack

LEN_HEADER = struct.Struct(">I")
MAX_FRAME_BYTES = 64 * 1024 * 1024  # 64 MB — generous for prefill activations


async def read_msg(reader: asyncio.StreamReader) -> dict[str, Any]:
    header = await reader.readexactly(LEN_HEADER.size)
    (n,) = LEN_HEADER.unpack(header)
    if n > MAX_FRAME_BYTES:
        raise ValueError(f"frame too large: {n} bytes")
    payload = await reader.readexactly(n)
    return msgpack.unpackb(payload, raw=False)


async def write_msg(writer: asyncio.StreamWriter, msg: dict[str, Any]) -> None:
    payload = msgpack.packb(msg, use_bin_type=True)
    writer.write(LEN_HEADER.pack(len(payload)))
    writer.write(payload)
    await writer.drain()

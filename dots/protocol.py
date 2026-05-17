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
import mlx.core as mx
import numpy as np

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


_BF16_WIRE = "bfloat16"


def encode_array(x: mx.array) -> dict[str, Any]:
    """mlx.array -> wire dict, lossless for every dtype we use.

    For bfloat16 we *bit-cast* to uint16 and ship the raw 16-bit pattern.
    The earlier scheme of casting bf16 → fp16 → bf16 truncated the lower
    3 mantissa bits on each round-trip; over a few hundred decode hops
    that drift was enough to push sampling into a repetition attractor
    after a re-prefill following a worker drop.
    """
    mx.eval(x)
    if x.dtype == mx.bfloat16:
        u16 = x.view(mx.uint16)
        arr = np.array(u16)
        return {
            "shape": list(arr.shape),
            "dtype": _BF16_WIRE,
            "data": arr.tobytes(),
        }
    arr = np.array(x)
    return {
        "shape": list(arr.shape),
        "dtype": str(arr.dtype),
        "data": arr.tobytes(),
    }


def decode_array(d: dict[str, Any]) -> mx.array:
    if d["dtype"] == _BF16_WIRE:
        arr = np.frombuffer(d["data"], dtype=np.uint16).reshape(d["shape"])
        return mx.array(arr.copy()).view(mx.bfloat16)
    arr = np.frombuffer(d["data"], dtype=np.dtype(d["dtype"])).reshape(d["shape"])
    return mx.array(arr.copy())

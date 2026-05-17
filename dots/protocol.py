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


def encode_array(x: mx.array) -> dict[str, Any]:
    """mlx.array -> wire dict. bfloat16 isn't representable in numpy, so we
    transport it as float16 and restore the original dtype on the other side."""
    mx.eval(x)
    orig_dtype = str(x.dtype).replace("mlx.core.", "")
    if x.dtype == mx.bfloat16:
        wire_x = x.astype(mx.float16)
        wire_dtype = "float16"
    else:
        wire_x = x
        wire_dtype = orig_dtype
    arr = np.array(wire_x)  # forced copy, safe across all mlx dtypes
    return {
        "shape": list(arr.shape),
        "dtype": wire_dtype,
        "orig_dtype": orig_dtype,
        "data": arr.tobytes(),
    }


_MLX_DTYPES = {
    "float32": mx.float32, "float16": mx.float16, "bfloat16": mx.bfloat16,
    "int32": mx.int32, "int8": mx.int8, "uint8": mx.uint8, "int64": mx.int64,
}


def decode_array(d: dict[str, Any]) -> mx.array:
    arr = np.frombuffer(d["data"], dtype=np.dtype(d["dtype"])).reshape(d["shape"])
    # frombuffer returns a read-only view; copy so mlx owns the memory.
    out = mx.array(arr.copy())
    orig = d.get("orig_dtype")
    if orig and orig != d["dtype"] and orig in _MLX_DTYPES:
        out = out.astype(_MLX_DTYPES[orig])
    return out

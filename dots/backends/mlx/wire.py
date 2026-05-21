"""MLX hidden-state wire encoding."""

from __future__ import annotations

from typing import Any

import mlx.core as mx
import numpy as np

_BF16_WIRE = "bfloat16"


def encode_mlx_array(x: mx.array) -> dict[str, Any]:
    """mlx.array -> wire dict, lossless for every dtype we use."""
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


def decode_mlx_array(d: dict[str, Any]) -> mx.array:
    if d["dtype"] == _BF16_WIRE:
        arr = np.frombuffer(d["data"], dtype=np.uint16).reshape(d["shape"])
        return mx.array(arr.copy()).view(mx.bfloat16)
    arr = np.frombuffer(d["data"], dtype=np.dtype(d["dtype"])).reshape(d["shape"])
    return mx.array(arr.copy())

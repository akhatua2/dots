"""Compatibility shim for ``dots.distributed.protocol``."""

from .distributed.protocol import *  # noqa: F401,F403
from .backends.mlx.wire import (  # noqa: F401
    decode_mlx_array,
    decode_mlx_array as decode_array,
    encode_mlx_array,
    encode_mlx_array as encode_array,
)

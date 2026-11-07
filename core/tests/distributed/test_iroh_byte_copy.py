"""The iroh bindings hand bytes to Rust in one copy, and the bytes are the same ones."""

import time

from iroh import iroh_ffi

import dots.distributed.iroh_net  # noqa: F401  (installs the one-copy write)


def written(data: bytes) -> bytes:
    builder = iroh_ffi._UniffiRustBufferBuilder()
    builder.write(data)
    rbuf = builder.finalize()
    try:
        return bytes(rbuf.data[: rbuf.len])
    finally:
        rbuf.free()


def test_bytes_arrive_exactly_as_given():
    data = bytes(i % 251 for i in range(100_003))
    assert written(data) == data
    assert written(b"") == b""


def test_a_megabyte_takes_milliseconds_not_a_fraction_of_a_second():
    data = bytes(1_000_000)
    started = time.perf_counter()
    written(data)
    assert time.perf_counter() - started < 0.05, "the byte-at-a-time loop took about 170 ms for this"

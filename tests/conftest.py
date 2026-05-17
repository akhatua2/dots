"""Pytest fixtures.

The `mock_model` autouse fixture replaces mlx-lm's model + tokenizer + cache
+ attention-mask with the fakes in tests/fakes.py, so every test runs
without loading an actual transformer. Tests that genuinely need a real
model can opt out by parameterizing or by calling the real functions
directly — none of the current tests do.
"""
from __future__ import annotations

import asyncio
import socket
from typing import AsyncIterator

import pytest
import pytest_asyncio

from dots.hub import Hub
from dots.peer import PeerConnection
from dots.util import free_ram_gb
from dots.worker import Worker

from .fakes import (
    fake_load, fake_make_prompt_cache, fake_create_attention_mask,
)

TEST_MODEL = "fake/test-model"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def mock_model(monkeypatch):
    monkeypatch.setattr("dots.hub.mlx_load", fake_load)
    monkeypatch.setattr("dots.shard.mlx_load", fake_load)
    monkeypatch.setattr("dots.shard.make_prompt_cache", fake_make_prompt_cache)
    monkeypatch.setattr("dots.shard.create_attention_mask", fake_create_attention_mask)


@pytest_asyncio.fixture
async def hub() -> AsyncIterator[Hub]:
    """Start a hub on a random local port, yield it, then shut down all
    associated worker connections + the hub's server task."""
    port = _free_port()
    h = Hub(port=port, model_id=TEST_MODEL, host="127.0.0.1")
    task = asyncio.create_task(h.start())
    try:
        await asyncio.wait_for(h.started.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        task.cancel()
        raise TimeoutError("hub did not start within 5s")
    try:
        yield h
    finally:
        # Close any worker connections still attached to the hub — their
        # _on_connect coroutines block on conn._closed.wait() and would
        # otherwise keep the hub task alive past cancellation.
        for w in list(h.participants.values()):
            try:
                await w.conn.close()
            except Exception:
                pass
        task.cancel()
        # Give cancellation one tick to propagate but don't block forever
        # on the server's wait_closed (it may stall on lingering reader tasks).
        try:
            await asyncio.wait_for(task, timeout=0.5)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass


# Tests need to insert a fresh worker into the hub. Using Worker.start()
# would block on its chat REPL, so we drive the connect-and-load prefix
# manually and return a connected Worker that callers can issue send_prompt
# against.
async def connect_worker(hub: Hub) -> Worker:
    w = Worker("127.0.0.1", hub.port)
    reader, writer = await asyncio.open_connection("127.0.0.1", hub.port)
    w.conn = PeerConnection(reader, writer, f"127.0.0.1:{hub.port}", w._handle)
    ack = await w.conn.call("hello", free_ram_gb=free_ram_gb())
    assert ack.get("kind") == "hello_ack"
    w.model_id = ack["model_id"]
    w.n_layers = ack["n_layers"]
    await asyncio.wait_for(w._welcome.wait(), timeout=2.0)
    # Wait for the shard to be loaded.
    deadline = asyncio.get_running_loop().time() + 2.0
    while w.shard is None and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    assert w.shard is not None, "worker shard did not load"
    return w


async def wait_for_pool_size(hub: Hub, size: int, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if sum(1 for w in hub.participants.values() if w.ready) == size:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(f"pool didn't reach {size} ready workers in {timeout}s")

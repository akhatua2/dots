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
import uuid
from typing import AsyncIterator

import pytest
import pytest_asyncio

from dots.hub import Hub
from dots.peer import PeerConnection
from dots.pool import resource_decl_to_hello_field
from dots.scheduler import ResourceDecl
from dots.util import effective_tflops_estimate, free_ram_gb, supported_backends
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
    # Workers load the full model via mlx_load (now an attribute on
    # dots.worker for monkey-patchability) and use the attention-mask /
    # prompt-cache builders inside dots.inference.
    monkeypatch.setattr("dots.worker.mlx_load", fake_load)
    monkeypatch.setattr("dots.inference.make_prompt_cache", fake_make_prompt_cache)
    monkeypatch.setattr("dots.inference.create_attention_mask", fake_create_attention_mask)


@pytest_asyncio.fixture
async def hub(tmp_path) -> AsyncIterator[Hub]:
    """Start a hub on a random local port, yield it, then shut down all
    associated worker connections + the hub's server task.

    Ledger path is redirected to a per-test tmp dir so test turns can
    record real CU without polluting the developer's ~/.dots/."""
    port = _free_port()
    h = Hub(port=port, model_id=TEST_MODEL, host="127.0.0.1",
            ledger_path=tmp_path / "credits.json")
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
    # Give every test-spawned worker a unique identity AND account so
    # multiple workers in the same test don't share the on-disk
    # ~/.dots/{identity,account} files (which would either trigger
    # reconnect-takeover or accidentally pollute the developer's real
    # account file).
    w = Worker(
        "127.0.0.1", hub.port,
        identity=str(uuid.uuid4()),
        account_id="acc-" + uuid.uuid4().hex,
        account_alias="",
    )
    # Start the worker's peer-listen server so probes from the hub work.
    await w._start_peer_server()
    reader, writer = await asyncio.open_connection("127.0.0.1", hub.port)
    w.conn = PeerConnection(reader, writer, f"127.0.0.1:{hub.port}", w._handle)
    # Build a real ResourceDecl so the hub's new pool_view ingests properly.
    decl = ResourceDecl(
        worker_id=w.identity,
        account_id=w.account_id,
        ram_gb_free=free_ram_gb(),
        compute_tflops_per_sec=effective_tflops_estimate(),
        backends=supported_backends(),
        cluster_id="",
    )
    ack = await w.conn.call(
        "hello", worker_id=w.identity, account_id=w.account_id,
        free_ram_gb=decl.ram_gb_free,
        resources=resource_decl_to_hello_field(decl),
        peer_listen_port=w._peer_listen_port,
    )
    assert ack.get("kind") == "hello_ack"
    w.model_id = ack["model_id"]
    w.n_layers = ack["n_layers"]
    # Manually kick off the model load (in the real worker this happens
    # right after HELLO_ACK in `connect()`; tests drive HELLO themselves).
    w._host_load_task = asyncio.create_task(w._load_host_async(w.model_id))
    await asyncio.wait_for(w._welcome.wait(), timeout=2.0)
    # Wait for the worker's host to finish loading + worker_ready to arrive
    # at the hub. The hub fixture sets `p.ready = True` on receipt.
    await asyncio.wait_for(w._host_ready.wait(), timeout=5.0)
    return w


async def wait_for_pool_size(hub: Hub, size: int, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if sum(1 for w in hub.participants.values() if w.ready) == size:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(f"pool didn't reach {size} ready workers in {timeout}s")

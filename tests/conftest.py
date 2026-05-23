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

from dots.distributed.hub import Hub
from dots.distributed.peer import PeerConnection
from dots.distributed.protocol import InMemoryTransport
from dots.distributed.pool import resource_decl_to_hello_field
from dots.backends.base import ModelMetadata
from dots.system.util import effective_tflops_estimate, free_ram_gb
from dots.distributed.worker import Worker, _ignore_inbound
from tests.helpers.resources import resource_decl

from .fakes import (
    FakeTensor,
    fake_argmax_token,
    fake_array,
    fake_load,
    fake_make_prompt_cache,
    fake_create_attention_mask,
)
from tests.helpers.models import exact_geometry

TEST_MODEL = "fake/test-model"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", 0))
        except PermissionError as e:
            pytest.skip(f"localhost bind unavailable in this test environment: {e}")
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def mock_model(monkeypatch):
    # Tests use a faked load path: worker.load_partial returns a fake
    # model + tokenizer + layer count without touching mlx_lm. Hub's
    # config-priming step is also stubbed (no HF download in tests).
    def fake_partial(model_id, *, layer_start=0, layer_end=None, needs_head=True):
        from .fakes import DEFAULT_LAYERS

        m, t = fake_load(model_id)
        return m, t, DEFAULT_LAYERS

    monkeypatch.setattr("dots.backends.mlx.runtime.load_partial", fake_partial)
    monkeypatch.setattr(
        "dots.backends.registry.available_backend_names",
        lambda: frozenset({"mlx"}),
    )
    monkeypatch.setattr(
        "dots.backends.registry.is_backend_available",
        lambda name: name == "mlx",
    )
    monkeypatch.setattr(
        "dots.backends.mlx.runtime.MlxBackendRuntime.metadata_for",
        _fake_metadata_for,
    )
    monkeypatch.setattr(
        "dots.backends.mlx.runtime.safetensors_disk_size_gb",
        lambda _model_id: 0.001,
    )
    monkeypatch.setattr(
        "dots.backends.mlx.runtime.make_prompt_cache", fake_make_prompt_cache
    )
    monkeypatch.setattr(
        "dots.backends.mlx.runtime.create_attention_mask", fake_create_attention_mask
    )
    monkeypatch.setattr("dots.backends.mlx.runtime.array", fake_array)
    monkeypatch.setattr("dots.backends.mlx.runtime.argmax_token", fake_argmax_token)
    # The repetition penalty does real mlx tensor math (gather + where);
    # on FakeTensor logits it would blow up. Sampling is faked anyway, so
    # neutralize the penalty to a pass-through for these tests. Its real
    # behavior is covered in tests/backends/test_mlx_sampling.py.
    monkeypatch.setattr(
        "dots.backends.mlx.runtime._apply_repetition_penalty",
        lambda logits_row, recent_tokens, penalty: logits_row,
    )
    monkeypatch.setattr(
        "dots.backends.mlx.runtime.MlxBackendRuntime.encode_hidden",
        lambda _self, hidden: hidden.to_wire(),
    )
    monkeypatch.setattr(
        "dots.backends.mlx.runtime.MlxBackendRuntime.decode_hidden",
        lambda _self, payload: FakeTensor.from_wire(payload),
    )

    # Stub the hub's eager geometry primer so the hub fixture doesn't
    # try to download a real model on test setup.
    async def fake_prime(self):
        from .fakes import DEFAULT_LAYERS, HIDDEN_DIM, VOCAB_SIZE

        self._selected_backend = "mlx"
        self.n_layers = DEFAULT_LAYERS
        self._geometry = exact_geometry(
            n_layers=DEFAULT_LAYERS,
            hidden_size=HIDDEN_DIM,
            vocab_size=VOCAB_SIZE,
        )

    monkeypatch.setattr(
        "dots.distributed.hub.Hub._prime_geometry_from_config", fake_prime
    )


async def _fake_metadata_for(self, model_id: str) -> ModelMetadata:
    from .fakes import DEFAULT_LAYERS, HIDDEN_DIM, VOCAB_SIZE

    return ModelMetadata(
        source_id=model_id,
        runtime_id=model_id,
        geometry=exact_geometry(
            n_layers=DEFAULT_LAYERS,
            hidden_size=HIDDEN_DIM,
            vocab_size=VOCAB_SIZE,
            layer_ram_gb=0.001,
            embed_ram_gb=0.001,
            lm_head_ram_gb=0.001,
        ),
    )


@pytest_asyncio.fixture
async def hub(tmp_path) -> AsyncIterator[Hub]:
    """Start a hub on a random local port, yield it, then shut down all
    associated worker connections + the hub's server task.

    Ledger path is redirected to a per-test tmp dir so test turns can
    record real CU without polluting the developer's ~/.dots/."""
    h = Hub(
        port=0,
        model_id=TEST_MODEL,
        host="127.0.0.1",
        ledger_path=tmp_path / "credits.json",
    )
    # Tests drive the hub directly over in-memory transports (see
    # connect_worker) rather than standing up the real iroh listener — that
    # keeps the suite fast and deterministic, with no sockets or NAT. Mark it
    # "started" for code paths that wait on it.
    h.started.set()
    h._test_serve_tasks = []
    try:
        yield h
    finally:
        # Close worker connections so each hub-side _serve_peer coroutine
        # (which blocks on conn._closed.wait()) returns, then reap the tasks.
        for w in list(h.participants.values()):
            try:
                await w.conn.close()
            except Exception:
                pass
        for t in h._test_serve_tasks:
            t.cancel()
        for t in h._test_serve_tasks:
            try:
                await asyncio.wait_for(t, timeout=0.5)
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
        "test-hub-ticket",
        identity=str(uuid.uuid4()),
        account_id="acc-" + uuid.uuid4().hex,
        account_alias="",
    )
    # Wire worker <-> hub over an in-memory transport pair (no sockets, no
    # iroh). The hub serves its end for the connection's lifetime, exactly as
    # the real iroh ALPN listener would.
    hub_side, worker_side = InMemoryTransport.pair()
    serve_task = asyncio.create_task(hub._serve_peer(hub_side, w.identity[:16]))
    hub._test_serve_tasks.append(serve_task)
    w.conn = PeerConnection(worker_side, "hub", w._handle)
    # Build a real ResourceDecl so the hub's new pool_view ingests properly.
    decl = resource_decl(
        w.identity,
        account_id=w.account_id,
        ram_gb=free_ram_gb(),
        tflops=effective_tflops_estimate(),
        backends=frozenset({"mlx"}),
        cluster_id="",
    )
    ack = await w.conn.call(
        "hello",
        worker_id=w.identity,
        account_id=w.account_id,
        free_ram_gb=decl.ram_gb_free,
        resources=resource_decl_to_hello_field(decl),
        # A compute worker must advertise a peer ticket; tests don't form real
        # worker<->worker connections, so a placeholder is fine.
        peer_ticket="test-ticket-" + w.identity[:8],
    )
    assert ack.get("kind") == "hello_ack"
    # The hub is model-agnostic and no longer advertises a model in the
    # ack — the querier names it on each request. Tests drive a single
    # model, so pin this worker to TEST_MODEL (and set requested_model_id
    # so send_prompt includes the now-required model_id).
    w.model_id = TEST_MODEL
    w.requested_model_id = TEST_MODEL
    w.n_layers = hub._deployment_for(TEST_MODEL).n_layers
    # In the new lifecycle the hub broadcasts `load_units` after the
    # worker joins; the worker handler kicks off load_partial. So we
    # just wait for both WELCOME and the resulting _host_ready.
    await asyncio.wait_for(w._welcome.wait(), timeout=2.0)
    await asyncio.wait_for(w._host_ready.wait(), timeout=5.0)
    return w


async def connect_client(hub: Hub, *, model_id: str, identity: str | None = None) -> Worker:
    """Connect a chat-only client (compute=False) over an in-memory transport.

    The compute-worker equivalent is `connect_worker`; this one contributes no
    compute, so it advertises no peer ticket and never loads a model.
    """
    cid = identity or str(uuid.uuid4())
    client = Worker(
        "test-hub-ticket",
        identity=cid,
        account_id="acc-" + cid[:8],
        account_alias="",
        compute=False,
        requested_model_id=model_id,
    )
    hub_side, worker_side = InMemoryTransport.pair()
    hub._test_serve_tasks.append(
        asyncio.create_task(hub._serve_peer(hub_side, cid[:16]))
    )
    client.conn = PeerConnection(worker_side, "hub", client._handle)
    await client.conn.call(
        "hello",
        worker_id=cid,
        account_id=client.account_id,
        compute=False,
        resources=resource_decl_to_hello_field(
            resource_decl(
                cid,
                account_id=client.account_id,
                ram_gb=4.0,
                tflops=0.0,
                backends=frozenset(),
                cluster_id="",
            )
        ),
    )
    await asyncio.wait_for(client._welcome.wait(), timeout=2.0)
    return client


async def wire_peer_link(head: Worker, peer: Worker) -> None:
    """Establish a direct worker<->worker data-plane link over an in-memory
    transport — what iroh would hole-punch for a multi-worker chain.

    Pre-seeds the head's cached outgoing connection to `peer` so the head's
    `_get_or_open_peer` returns it instead of dialing iroh (which tests don't
    run); the far end is served by `peer`'s data-plane handler.
    """
    head_side, peer_side = InMemoryTransport.pair()
    head._peer_conns[peer.identity] = PeerConnection(
        head_side, peer.identity[:16], _ignore_inbound
    )
    serve = PeerConnection(peer_side, head.identity[:16], peer._handle_peer_msg)
    # Keep a reference so the serving channel (and its read loop) isn't GC'd.
    peer._test_peer_links = getattr(peer, "_test_peer_links", [])
    peer._test_peer_links.append(serve)


async def wait_for_pool_size(hub: Hub, size: int, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if sum(1 for w in hub.participants.values() if w.ready) == size:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(f"pool didn't reach {size} ready workers in {timeout}s")

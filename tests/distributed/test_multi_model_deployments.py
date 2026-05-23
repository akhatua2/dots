from __future__ import annotations

import asyncio
import uuid

import pytest

from dots.distributed.hub import Hub, ModelWorkerState, Participant
from dots.distributed.peer import PeerConnection
from dots.distributed.pool import resource_decl_to_hello_field
from dots.distributed.worker import Worker
from tests.conftest import connect_client, connect_worker
from tests.helpers.models import exact_geometry
from tests.helpers.resources import resource_decl


pytestmark = pytest.mark.asyncio

ALT_MODEL = "fake/alt-model"


async def _wait_for_deployment_ready(
    hub: Hub,
    model_id: str,
    timeout: float = 5.0,
) -> None:
    async def _poll() -> None:
        while not hub.deployments[model_id].pool_ready.is_set():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout=timeout)


async def _connect_chat_client(hub: Hub, *, model_id: str) -> Worker:
    # Chat-only client wired over an in-memory transport (see conftest).
    return await connect_client(hub, model_id=model_id)


async def test_prompt_model_id_creates_separate_deployment(hub: Hub):
    worker = await connect_worker(hub)

    status = await worker.get_pool_status(model_id=ALT_MODEL)
    assert status["kind"] == "status"
    if not status["ready"]:
        assert status["state"] in {"loading", "loading_peers", "waiting"}

    await asyncio.wait_for(worker._ready_event_for(ALT_MODEL).wait(), timeout=5.0)
    await _wait_for_deployment_ready(hub, ALT_MODEL)

    text, stats = await worker.send_prompt(
        "hello from alt",
        max_tokens=3,
        temperature=0,
        model_id=ALT_MODEL,
    )

    assert text
    assert stats["model_id"] == ALT_MODEL
    assert set(hub.deployments) >= {"fake/test-model", ALT_MODEL}
    assert set(worker.backends) >= {"fake/test-model", ALT_MODEL}


async def test_serve_model_reuses_existing_deployment(hub: Hub):
    compute_worker = await connect_worker(hub)
    client = await _connect_chat_client(hub, model_id=ALT_MODEL)
    try:
        first = await client.serve_model(ALT_MODEL)
        assert first["kind"] == "serve_model_ack"
        assert first["model_id"] == ALT_MODEL

        await asyncio.wait_for(
            compute_worker._ready_event_for(ALT_MODEL).wait(),
            timeout=5.0,
        )
        await _wait_for_deployment_ready(hub, ALT_MODEL)

        deployment = hub.deployments[ALT_MODEL]
        second = await client.serve_model(ALT_MODEL)
        assert second["kind"] == "serve_model_ack"
        assert second["ready"] is True
        assert hub.deployments[ALT_MODEL] is deployment
        assert list(hub.deployments).count(ALT_MODEL) == 1
    finally:
        if client.conn is not None:
            await client.conn.close()


async def test_chat_requested_model_auto_starts_deployment(hub: Hub):
    compute_worker = await connect_worker(hub)
    chat = await _connect_chat_client(hub, model_id=ALT_MODEL)
    try:
        status = await chat.get_pool_status()
        assert status["kind"] == "status"

        await asyncio.wait_for(
            compute_worker._ready_event_for(ALT_MODEL).wait(),
            timeout=5.0,
        )
        await _wait_for_deployment_ready(hub, ALT_MODEL)

        text, stats = await chat.send_prompt(
            "auto serve this model",
            max_tokens=2,
            temperature=0,
        )

        assert text
        assert stats["model_id"] == ALT_MODEL
        assert ALT_MODEL in hub.deployments
    finally:
        if chat.conn is not None:
            await chat.conn.close()


async def test_second_model_placement_subtracts_existing_model_ram():
    hub = Hub(port=0, model_id="model-a")

    class _StubConn:
        async def notify(self, *_args, **_kwargs):
            return None

    worker_a = Participant(
        id="worker-a",
        addr="127.0.0.1:1",
        conn=_StubConn(),
        compute=True,
        peer_ticket="test-ticket",
    )
    worker_b = Participant(
        id="worker-b",
        addr="127.0.0.1:2",
        conn=_StubConn(),
        compute=True,
        peer_ticket="test-ticket",
    )
    hub.participants[worker_a.id] = worker_a
    hub.participants[worker_b.id] = worker_b
    hub.pool_view.add_or_update(
        worker_a.id,
        resource_decl(worker_a.id, ram_gb=8.0, tflops=1.0),
    )
    hub.pool_view.add_or_update(
        worker_b.id,
        resource_decl(worker_b.id, ram_gb=8.0, tflops=1.0),
    )

    model_a = hub._deployment_for("model-a")
    model_a.selected_backend = "mlx"
    model_a.geometry = exact_geometry(
        n_layers=2,
        layer_ram_gb=3.0,
        embed_ram_gb=0.5,
    )
    model_a.n_layers = 2
    model_a.current_placement = [("worker-a", 0, 2, True)]

    hub._activate_model("model-b")
    hub._selected_backend = "mlx"
    hub._geometry = exact_geometry(
        n_layers=2,
        layer_ram_gb=1.0,
        embed_ram_gb=1.0,
    )
    hub.n_layers = 2

    assignments = hub._assign_units_to_workers()

    assert assignments == [("worker-b", 0, 2, True)]


class _RecordingConn:
    """Stub connection that records `unload_model` calls."""

    def __init__(self):
        self.unloaded: list[str] = []

    async def call(self, kind, **payload):
        if kind == "unload_model":
            self.unloaded.append(payload.get("model_id"))
        return {"kind": "unload_ack", "model_id": payload.get("model_id")}

    async def notify(self, *_args, **_kwargs):
        return None


async def test_evict_idle_deployments_unloads_only_unqueried_models():
    """A model no connected client is querying gets unloaded from its
    worker (RAM reclaimed) and dropped from the hub. A model a client is
    actively querying is protected and left alone."""
    hub = Hub(port=0)
    conn = _RecordingConn()
    worker = Participant(
        id="w1",
        addr="127.0.0.1:1",
        conn=conn,
        compute=True,
        peer_ticket="test-ticket",
    )
    hub.participants["w1"] = worker

    # An idle deployment: loaded on the worker, no client wants it.
    idle = hub._deployment_for("model-idle")
    idle.current_placement = [("w1", 0, 2, True)]
    idle.worker_states["w1"] = ModelWorkerState()
    idle.last_used_at = 1.0

    # A deployment a connected client is actively querying → protected.
    active = hub._deployment_for("model-active")
    active.current_placement = [("w1", 0, 2, True)]
    hub.participants["c1"] = Participant(
        id="c1",
        addr="127.0.0.1:2",
        conn=_RecordingConn(),
        compute=False,
        requested_model_id="model-active",
    )

    evicted = await hub._evict_idle_deployments(keep="model-new")

    assert evicted is True
    assert conn.unloaded == ["model-idle"], "only the idle model should unload"
    assert "model-idle" not in hub.deployments, "evicted deployment is dropped"
    assert "model-active" in hub.deployments, "queried model must be kept"


async def test_evict_idle_deployments_noop_when_all_protected():
    """If every loaded model is either being queried or is the one we're
    making room for, nothing is evicted."""
    hub = Hub(port=0)
    conn = _RecordingConn()
    hub.participants["w1"] = Participant(
        id="w1",
        addr="127.0.0.1:1",
        conn=conn,
        compute=True,
        peer_ticket="test-ticket",
    )
    keep = hub._deployment_for("model-new")
    keep.current_placement = [("w1", 0, 2, True)]

    evicted = await hub._evict_idle_deployments(keep="model-new")

    assert evicted is False
    assert conn.unloaded == []
    assert "model-new" in hub.deployments


async def test_worker_unload_model_drops_backend(hub: Hub):
    """The worker's unload handler releases the runtime and forgets the
    model so its RAM frees up — covering the worker side of eviction."""
    worker = await connect_worker(hub)
    # Bring up a second model on the worker (same flow as a real prompt).
    await worker.get_pool_status(model_id=ALT_MODEL)
    await asyncio.wait_for(worker._ready_event_for(ALT_MODEL).wait(), timeout=5.0)
    assert ALT_MODEL in worker.backends

    ack = await worker._handle({"kind": "unload_model", "model_id": ALT_MODEL})

    assert ack == {"kind": "unload_ack", "model_id": ALT_MODEL}
    assert ALT_MODEL not in worker.backends
    # The other model is untouched.
    assert "fake/test-model" in worker.backends

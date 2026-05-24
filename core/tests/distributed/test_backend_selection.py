from __future__ import annotations

import pytest

from dots.backends.base import BackendCapabilities, PreparationRequirement
from dots.distributed.hub import Hub, Participant
from dots.distributed.worker import Worker
from dots.system.resources import WorkerResourceLimits
from tests.helpers.models import exact_geometry
from tests.helpers.protocol import RecordingConnection
from tests.helpers.resources import resource_decl


def _participant(worker_id: str, conn=None) -> Participant:
    return Participant(
        id=worker_id,
        addr="127.0.0.1:10000",
        conn=conn or RecordingConnection(),
        compute=True,
    )


def test_backend_candidates_follow_registry_order():
    hub = Hub(port=0, model_id="fake/model")
    hub.participants["cpu"] = _participant("cpu")
    hub.participants["mac"] = _participant("mac")
    hub.pool_view.add_or_update(
        "cpu",
        resource_decl("cpu", backends=frozenset({"llama_cpp"})),
    )
    hub.pool_view.add_or_update(
        "mac",
        resource_decl("mac", backends=frozenset({"mlx"})),
    )

    assert hub._backend_candidates_from_pool() == ["mlx", "llama_cpp"]


def test_assignment_filters_workers_by_selected_backend():
    hub = Hub(port=0, model_id="fake/model")
    hub._selected_backend = "mlx"
    hub._geometry = exact_geometry(n_layers=4)
    hub.n_layers = 4

    hub.participants["mlx-worker"] = _participant("mlx-worker")
    hub.participants["cpu-worker"] = _participant("cpu-worker")
    hub.pool_view.add_or_update(
        "mlx-worker",
        resource_decl("mlx-worker", ram_gb=8.0, backends=frozenset({"mlx"})),
    )
    hub.pool_view.add_or_update(
        "cpu-worker",
        resource_decl("cpu-worker", ram_gb=128.0, backends=frozenset({"llama_cpp"})),
    )

    assignments = hub._assign_units_to_workers()

    assert assignments == [("mlx-worker", 0, 4, True)]


@pytest.mark.asyncio
async def test_load_units_includes_selected_backend():
    conn = RecordingConnection()
    hub = Hub(port=0, model_id="fake/model")
    hub._selected_backend = "mlx"
    hub.participants["w1"] = _participant("w1", conn=conn)
    hub.pool_view.add_or_update("w1", resource_decl("w1", backends=frozenset({"mlx"})))

    await hub._broadcast_load_units([("w1", 0, 4, True)])

    assert conn.notifications == [
        (
            "load_units",
            {
                "backend": "mlx",
                "model_id": "fake/model",
                "layer_start": 0,
                "layer_end": 4,
                "needs_head": True,
            },
        )
    ]


@pytest.mark.asyncio
async def test_worker_refuses_load_when_preparation_exceeds_disk_limit(monkeypatch):
    class _Runtime:
        capabilities = BackendCapabilities(name="mlx")

        async def preparation_requirement(self, *_args, **_kwargs):
            return PreparationRequirement(
                needs_conversion=False,
                disk_gb=2.0,
                ram_gb=0.5,
                reason="test oversized artifact",
            )

    runtime = _Runtime()
    monkeypatch.setattr(
        "dots.distributed.worker.get_backend_runtime",
        lambda _name: runtime,
    )
    worker = Worker(
        "127.0.0.1",
        5555,
        identity="worker-1",
        account_id="acc-worker",
        account_alias="",
    )
    worker.resource_limits = WorkerResourceLimits(
        ram_gb=8.0,
        disk_gb=1.0,
        cpu_utilization=0.5,
        io_read_gbps=1.0,
        io_write_gbps=1.0,
        compute_tflops_per_sec=1.0,
        backends=frozenset({"mlx"}),
    )

    await worker._handle_load_units(
        {
            "backend": "mlx",
            "model_id": "org/model",
            "layer_start": 0,
            "layer_end": 2,
            "needs_head": True,
        }
    )

    assert worker._host_load_task is None
    assert not worker._host_ready.is_set()

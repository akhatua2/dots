"""The hub's pool-map list says what each worker offers and whether it is answering right now."""

from types import SimpleNamespace

from dots.app.api import HttpApi


def test_pool_nodes_report_each_workers_memory():
    worker = SimpleNamespace(id="w1", account_id="acc", account_alias=None, ready=True, backend="mlx",
                             device="MacBook Pro", requested_model_id="m", layer_range=(0, 12), geo=None, compute=True, pool="")
    hub = SimpleNamespace(participants={"w1": worker}, is_serving=lambda wid: wid == "w1",
                          pool_view=SimpleNamespace(resources={"w1": SimpleNamespace(ram_gb_free=6.04)}))
    (node,) = HttpApi(hub)._pool_nodes_data()
    assert node["ram_gb"] == 6.0 and node["layer_range"] == [0, 12]
    assert node["serving"] is True


def test_a_worker_without_a_declaration_has_no_memory_figure():
    worker = SimpleNamespace(id="w2", account_id=None, account_alias=None, ready=False, backend=None,
                             device=None, requested_model_id=None, layer_range=None, geo=None, compute=True, pool="")
    hub = SimpleNamespace(participants={"w2": worker}, is_serving=lambda _wid: False,
                          pool_view=SimpleNamespace(resources={}))
    assert HttpApi(hub)._pool_nodes_data()[0]["ram_gb"] is None

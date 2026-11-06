"""A model that fits on one Mac runs there: a second Mac joining never splits a turn across the network."""

from __future__ import annotations

from dots.distributed.hub import Hub, Participant
from tests.helpers.models import exact_geometry
from tests.helpers.resources import resource_decl

MODEL = "model-a"
LAYERS = 24


class _Conn:
    async def notify(self, *_args, **_kwargs):
        return None


def _hub_with(workers: dict[str, float]) -> Hub:
    """A hub serving MODEL, with each worker (name -> free GB) ready and holding the whole model."""
    hub = Hub(port=0, model_id=MODEL)
    hub._activate_model(MODEL)
    deployment = hub._deployment_for(MODEL)
    deployment.selected_backend = "mlx"
    deployment.geometry = exact_geometry(n_layers=LAYERS, layer_ram_gb=0.02, embed_ram_gb=0.1)
    deployment.n_layers = LAYERS
    for name, gb in workers.items():
        hub.participants[name] = Participant(id=name, addr=f"{name}:1", conn=_Conn(), compute=True,
                                             peer_ticket=f"ticket-{name}", ready=True, backend="mlx",
                                             layer_range=(0, LAYERS), account_id=f"acc-{name}")
        hub.pool_view.add_or_update(name, resource_decl(name, account_id=f"acc-{name}", ram_gb=gb, tflops=1.0))
    deployment.current_placement = [(name, 0, LAYERS, True) for name in workers]
    return hub


def test_two_macs_with_the_whole_model_each_answer_alone():
    hub = _hub_with({"mac-a": 8.0, "mac-b": 16.0})
    head, layer_end, chain = hub._build_chain_spec(querier_account="api")
    assert head is not None and layer_end == LAYERS and chain == [], "one Mac answers; no hop"


def test_your_own_mac_answers_your_question():
    hub = _hub_with({"mac-a": 8.0, "mac-b": 16.0})
    head, _, chain = hub._build_chain_spec(querier_account="acc-mac-a")
    assert head.id == "mac-a" and chain == []


def test_three_macs_still_answer_alone():
    hub = _hub_with({"mac-a": 8.0, "mac-b": 16.0, "mac-c": 32.0})
    _, layer_end, chain = hub._build_chain_spec(querier_account="api")
    assert layer_end == LAYERS and chain == []


def test_a_small_mac_joining_leaves_the_model_where_it_fits():
    hub = _hub_with({"mac-a": 8.0})
    hub.participants["mac-small"] = Participant(id="mac-small", addr="small:1", conn=_Conn(), compute=True,
                                                peer_ticket="ticket-small", account_id="acc-small")
    hub.pool_view.add_or_update("mac-small", resource_decl("mac-small", account_id="acc-small", ram_gb=0.2))
    hub._selected_backend = "mlx"
    hub._geometry = hub._deployment_for(MODEL).geometry
    hub.n_layers = LAYERS
    assert hub._assign_units_to_workers() == [("mac-a", 0, LAYERS, True)], "the small Mac stays idle"

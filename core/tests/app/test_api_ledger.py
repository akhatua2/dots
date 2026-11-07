"""Turns answered through the HTTP API reach the ledger, charged to the account the caller names."""

from types import SimpleNamespace

from aiohttp.test_utils import make_mocked_request

from dots.app.api import HttpApi, _querier


def test_the_querier_is_the_named_account_or_api():
    assert _querier(make_mocked_request("POST", "/", headers={"X-Dots-Account": "acc-123_x"})) == "acc-123_x"
    assert _querier(make_mocked_request("POST", "/")) == "api"
    assert _querier(make_mocked_request("POST", "/", headers={"X-Dots-Account": "no spaces or ; allowed"})) == "api"


async def test_a_whole_answer_is_recorded_for_its_account():
    recorded = []

    async def run_turn(conv, **kwargs):
        assert kwargs["querier_account"] == "acc-me"
        return "Hello", {"prefill_tokens": 5, "decode_tokens": 2}

    hub = SimpleNamespace(run_turn=run_turn, record_api_turn=lambda *a: recorded.append(a))
    conv = SimpleNamespace(id="http-1")
    response = await HttpApi(hub)._nonstream(conv, "id", 0, 16, 0.7, "model", "acc-me")
    assert response.status == 200
    assert recorded == [(conv, "acc-me", {"prefill_tokens": 5, "decode_tokens": 2}, "model")]

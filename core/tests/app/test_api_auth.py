"""The hub HTTP API gates /v1/* on the shared join token and serves CORS.

These exercise the middleware in isolation (no live Hub) via a mocked request
and a stub handler, so they're fast and run in the PR gate.
"""

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from dots.app.api import _auth_cors_middleware


async def _ok(_request):
    return web.json_response({"ok": True})


async def test_options_preflight_is_open_and_cors():
    req = make_mocked_request("OPTIONS", "/v1/chat/completions")
    resp = await _auth_cors_middleware(req, _ok)
    assert resp.status == 204
    assert resp.headers["Access-Control-Allow-Origin"] == "*"
    assert "Authorization" in resp.headers["Access-Control-Allow-Headers"]


async def test_no_token_configured_is_open(monkeypatch):
    monkeypatch.delenv("DOTS_JOIN_TOKEN", raising=False)
    req = make_mocked_request("GET", "/v1/models")
    resp = await _auth_cors_middleware(req, _ok)
    assert resp.status == 200
    assert resp.headers["Access-Control-Allow-Origin"] == "*"


async def test_missing_token_rejected_when_configured(monkeypatch):
    monkeypatch.setenv("DOTS_JOIN_TOKEN", "secret")
    req = make_mocked_request("GET", "/v1/models")
    resp = await _auth_cors_middleware(req, _ok)
    assert resp.status == 401
    # Even the rejection carries CORS so the browser can read it.
    assert resp.headers["Access-Control-Allow-Origin"] == "*"


async def test_wrong_token_rejected(monkeypatch):
    monkeypatch.setenv("DOTS_JOIN_TOKEN", "secret")
    req = make_mocked_request(
        "GET", "/v1/models", headers={"Authorization": "Bearer nope"}
    )
    resp = await _auth_cors_middleware(req, _ok)
    assert resp.status == 401


async def test_correct_token_passes(monkeypatch):
    monkeypatch.setenv("DOTS_JOIN_TOKEN", "secret")
    req = make_mocked_request(
        "GET", "/v1/models", headers={"Authorization": "Bearer secret"}
    )
    resp = await _auth_cors_middleware(req, _ok)
    assert resp.status == 200
    assert resp.headers["Access-Control-Allow-Origin"] == "*"


async def test_healthz_open_even_with_token(monkeypatch):
    monkeypatch.setenv("DOTS_JOIN_TOKEN", "secret")
    req = make_mocked_request("GET", "/healthz")
    resp = await _auth_cors_middleware(req, _ok)
    assert resp.status == 200

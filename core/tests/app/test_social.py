"""Hub-side API keys and endorsements: scoped access, one-time secrets, persistence."""

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from dots.app.api import _auth_cors_middleware
from dots.app.social import SocialStore


async def _ok(_request):
    return web.json_response({"ok": True})


def _req(method, path, token, social):
    app = web.Application()
    app["social"] = social
    return make_mocked_request(
        method, path, headers={"Authorization": f"Bearer {token}"}, app=app
    )


def test_key_secret_is_returned_once_and_only_a_hash_is_stored(tmp_path):
    s = SocialStore(tmp_path / "social.json")
    record, secret = s.create_key("My scripts", ["pools"], 500)
    assert secret.startswith("dots_sk_live_")
    assert "hash" not in record and record["tail"] == secret[-4:]
    assert secret not in (tmp_path / "social.json").read_text()
    assert s.verify(secret)["id"] == record["id"]
    assert s.verify("dots_sk_live_wrong") is None


def test_revoke_removes_access(tmp_path):
    s = SocialStore(tmp_path / "social.json")
    record, secret = s.create_key("k", ["pools"], None)
    assert s.revoke_key(record["id"]) is True
    assert s.verify(secret) is None
    assert s.revoke_key(record["id"]) is False


def test_endorsements_are_per_account_and_idempotent(tmp_path):
    s = SocialStore(tmp_path / "social.json")
    assert s.endorse("hub", "acc-1") == 1
    assert s.endorse("hub", "acc-1") == 1
    assert s.endorse("hub", "acc-2") == 2
    assert s.unendorse("hub", "acc-1") == 1


def test_state_survives_a_restart(tmp_path):
    path = tmp_path / "social.json"
    a = SocialStore(path)
    _, secret = a.create_key("k", ["pools"], None)
    a.endorse("hub", "acc-1")
    b = SocialStore(path)
    assert b.verify(secret) is not None
    assert b.endorsements["hub"] == ["acc-1"]


async def test_minted_key_can_infer_but_not_manage(monkeypatch, tmp_path):
    monkeypatch.setenv("DOTS_JOIN_TOKEN", "owner-secret")
    s = SocialStore(tmp_path / "social.json")
    _, secret = s.create_key("k", ["pools"], None)

    ok = await _auth_cors_middleware(_req("POST", "/v1/chat/completions", secret, s), _ok)
    assert ok.status == 200
    assert s.keys[0]["requests"] == 1 and s.keys[0]["last_used"] is not None

    for method, path in [
        ("GET", "/v1/keys"),
        ("POST", "/v1/keys"),
        ("DELETE", f"/v1/keys/{s.keys[0]['id']}"),
        ("POST", "/v1/pools/hub/endorse"),
        ("GET", "/v1/credits"),
    ]:
        denied = await _auth_cors_middleware(_req(method, path, secret, s), _ok)
        assert denied.status == 401, (method, path)


async def test_owner_token_can_manage(monkeypatch, tmp_path):
    monkeypatch.setenv("DOTS_JOIN_TOKEN", "owner-secret")
    s = SocialStore(tmp_path / "social.json")
    resp = await _auth_cors_middleware(_req("GET", "/v1/keys", "owner-secret", s), _ok)
    assert resp.status == 200


async def test_revoked_key_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("DOTS_JOIN_TOKEN", "owner-secret")
    s = SocialStore(tmp_path / "social.json")
    record, secret = s.create_key("k", ["pools"], None)
    s.revoke_key(record["id"])
    resp = await _auth_cors_middleware(_req("POST", "/v1/chat/completions", secret, s), _ok)
    assert resp.status == 401

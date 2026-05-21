"""Unit tests for `dots.peer_auth`."""

from __future__ import annotations

import time

from dots.peer_auth import (
    IssuedToken,
    new_session_key,
    issue,
    verify,
)


def test_issued_token_roundtrip_dict():
    key = new_session_key()
    tok = issue(key, "hub-1", "W1", "W2", "conv-x", ("block_0", "block_1"))
    serialized = tok.to_dict()
    restored = IssuedToken.from_dict(serialized)
    assert restored == tok


def test_verify_accepts_matching_request():
    key = new_session_key()
    tok = issue(key, "hub-1", "W1", "W2", "conv-x", ("block_0",))
    result = verify(
        tok,
        key,
        expected_sender="W1",
        expected_receiver="W2",
        expected_conv_id="conv-x",
        expected_unit_id="block_0",
    )
    assert result.ok


def test_verify_rejects_wrong_signature():
    key1 = new_session_key()
    key2 = new_session_key()
    tok = issue(key1, "hub-1", "W1", "W2", "conv-x", ("block_0",))
    result = verify(
        tok,
        key2,
        expected_sender="W1",
        expected_receiver="W2",
        expected_conv_id="conv-x",
        expected_unit_id="block_0",
    )
    assert not result.ok
    assert "signature" in result.reason


def test_verify_rejects_expired():
    key = new_session_key()
    tok = issue(key, "hub-1", "W1", "W2", "conv-x", ("block_0",), ttl_seconds=1)
    later = time.time() + 5
    result = verify(
        tok,
        key,
        expected_sender="W1",
        expected_receiver="W2",
        expected_conv_id="conv-x",
        expected_unit_id="block_0",
        now_unix=later,
    )
    assert not result.ok
    assert "expired" in result.reason


def test_verify_rejects_sender_mismatch():
    key = new_session_key()
    tok = issue(key, "hub-1", "W1", "W2", "conv-x", ("block_0",))
    result = verify(
        tok,
        key,
        expected_sender="WX",
        expected_receiver="W2",
        expected_conv_id="conv-x",
        expected_unit_id="block_0",
    )
    assert not result.ok
    assert "sender" in result.reason


def test_verify_rejects_receiver_mismatch():
    key = new_session_key()
    tok = issue(key, "hub-1", "W1", "W2", "conv-x", ("block_0",))
    result = verify(
        tok,
        key,
        expected_sender="W1",
        expected_receiver="WX",
        expected_conv_id="conv-x",
        expected_unit_id="block_0",
    )
    assert not result.ok
    assert "receiver" in result.reason


def test_verify_rejects_conv_id_mismatch():
    key = new_session_key()
    tok = issue(key, "hub-1", "W1", "W2", "conv-x", ("block_0",))
    result = verify(
        tok,
        key,
        expected_sender="W1",
        expected_receiver="W2",
        expected_conv_id="conv-y",
        expected_unit_id="block_0",
    )
    assert not result.ok
    assert "conv_id" in result.reason


def test_verify_rejects_unscoped_unit():
    """A token scoped to block_0 cannot run block_1."""
    key = new_session_key()
    tok = issue(key, "hub-1", "W1", "W2", "conv-x", ("block_0",))
    result = verify(
        tok,
        key,
        expected_sender="W1",
        expected_receiver="W2",
        expected_conv_id="conv-x",
        expected_unit_id="block_1",
    )
    assert not result.ok
    assert "unit_id" in result.reason


def test_verify_rejects_tampered_scope():
    """Mutating the scope without re-signing breaks the signature."""
    key = new_session_key()
    tok = issue(key, "hub-1", "W1", "W2", "conv-x", ("block_0",))
    # Tamper with scope (give attacker an extra unit)
    from dots.peer_auth import Scope, IssuedToken as IT

    tampered_scope = Scope(
        issuer_id=tok.scope.issuer_id,
        sender_peer_id=tok.scope.sender_peer_id,
        receiver_peer_id=tok.scope.receiver_peer_id,
        conv_id=tok.scope.conv_id,
        allowed_unit_ids=("block_0", "block_1"),
        valid_until_unix=tok.scope.valid_until_unix,
    )
    tampered = IT(scope=tampered_scope, signature=tok.signature)
    result = verify(
        tampered,
        key,
        expected_sender="W1",
        expected_receiver="W2",
        expected_conv_id="conv-x",
        expected_unit_id="block_1",
    )
    assert not result.ok
    assert "signature" in result.reason

"""Session tokens for worker-to-worker auth.

When the hub introduces two peers (W₁ ↔ W₂) so they can talk directly for
the data plane (post-hybrid-pipeline), it issues a *session token* scoped
to that pair, that conversation, and a short TTL. The token is opaque to
the workers; verifying it just means HMAC-checking against the hub's
session key.

v1: HMAC-SHA256 over a canonical msgpack representation of the scope. The
hub's session key is a 32-byte secret generated at startup and rotated on
reboot. Workers receive tokens; they don't see the key.

Future: when [[cryptographic-ledger]] lands, this gets replaced by Ed25519
signatures keyed to each participant's identity key — no shared secret to
leak. The interface here is shaped to make that swap easy: `issue` /
`verify` / `Scope` data class.

This module is pure logic — no networking. Tested in isolation.
"""

from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass

import msgpack


SESSION_KEY_BYTES = 32  # 256-bit HMAC secret
DEFAULT_TTL_SECONDS = 300  # 5 min: short enough that revocation isn't needed


@dataclass(frozen=True)
class Scope:
    """The bounded authority granted by a session token.

    A token is valid only for the specific (issuer, peer_pair, conv) tuple
    AND only for the listed unit_ids on the peer being talked to. A peer
    receiving a `compute_unit` request checks both:
      * The token's signature matches the hub's session key.
      * The (conv_id, unit_id, sender, receiver) in the request matches
        the token's scope.
    Mismatches drop the connection silently.
    """

    issuer_id: str  # hub_id that issued the token
    sender_peer_id: str  # caller
    receiver_peer_id: str  # callee
    conv_id: str
    allowed_unit_ids: tuple[str, ...]  # which units callee may run
    valid_until_unix: float


@dataclass(frozen=True)
class IssuedToken:
    """A scope + its HMAC signature. Sent over the wire."""

    scope: Scope
    signature: bytes

    def to_dict(self) -> dict:
        return {
            "scope": {
                "issuer_id": self.scope.issuer_id,
                "sender_peer_id": self.scope.sender_peer_id,
                "receiver_peer_id": self.scope.receiver_peer_id,
                "conv_id": self.scope.conv_id,
                "allowed_unit_ids": list(self.scope.allowed_unit_ids),
                "valid_until_unix": self.scope.valid_until_unix,
            },
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "IssuedToken":
        s = d["scope"]
        scope = Scope(
            issuer_id=s["issuer_id"],
            sender_peer_id=s["sender_peer_id"],
            receiver_peer_id=s["receiver_peer_id"],
            conv_id=s["conv_id"],
            allowed_unit_ids=tuple(s["allowed_unit_ids"]),
            valid_until_unix=float(s["valid_until_unix"]),
        )
        return cls(scope=scope, signature=bytes(d["signature"]))


# ─────────────────────────── primitives ─────────────────────────────


def new_session_key() -> bytes:
    """Generate a fresh hub session key. Call once per hub lifetime."""
    return secrets.token_bytes(SESSION_KEY_BYTES)


def _canonical(scope: Scope) -> bytes:
    """Serialize scope deterministically so the HMAC is reproducible."""
    # Use msgpack with sorted keys for canonicalisation. Tuples become
    # lists in msgpack, which is fine — verify rebuilds with the same
    # representation.
    return msgpack.packb(
        {
            "issuer_id": scope.issuer_id,
            "sender_peer_id": scope.sender_peer_id,
            "receiver_peer_id": scope.receiver_peer_id,
            "conv_id": scope.conv_id,
            "allowed_unit_ids": list(scope.allowed_unit_ids),
            "valid_until_unix": scope.valid_until_unix,
        },
        use_bin_type=True,
    )


def issue(
    session_key: bytes,
    issuer_id: str,
    sender_peer_id: str,
    receiver_peer_id: str,
    conv_id: str,
    allowed_unit_ids: tuple[str, ...],
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
) -> IssuedToken:
    """Mint a fresh token for a (sender → receiver) peer pair."""
    scope = Scope(
        issuer_id=issuer_id,
        sender_peer_id=sender_peer_id,
        receiver_peer_id=receiver_peer_id,
        conv_id=conv_id,
        allowed_unit_ids=allowed_unit_ids,
        valid_until_unix=time.time() + ttl_seconds,
    )
    sig = hmac.new(session_key, _canonical(scope), "sha256").digest()
    return IssuedToken(scope=scope, signature=sig)


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of token verification. `ok=False` carries a diagnostic
    string — never expose this back over the wire (it leaks info to an
    attacker probing the protocol)."""

    ok: bool
    reason: str = ""


def verify(
    token: IssuedToken,
    session_key: bytes,
    expected_sender: str,
    expected_receiver: str,
    expected_conv_id: str,
    expected_unit_id: str,
    now_unix: float | None = None,
) -> VerifyResult:
    """Check the token is signed, in date, and matches the expected scope.

    `now_unix` is injectable for tests.
    """
    if now_unix is None:
        now_unix = time.time()

    expected_sig = hmac.new(session_key, _canonical(token.scope), "sha256").digest()
    if not hmac.compare_digest(expected_sig, token.signature):
        return VerifyResult(ok=False, reason="bad signature")

    s = token.scope
    if s.valid_until_unix < now_unix:
        return VerifyResult(ok=False, reason="expired")
    if s.sender_peer_id != expected_sender:
        return VerifyResult(ok=False, reason="sender mismatch")
    if s.receiver_peer_id != expected_receiver:
        return VerifyResult(ok=False, reason="receiver mismatch")
    if s.conv_id != expected_conv_id:
        return VerifyResult(ok=False, reason="conv_id mismatch")
    if expected_unit_id not in s.allowed_unit_ids:
        return VerifyResult(ok=False, reason="unit_id not in scope")
    return VerifyResult(ok=True)

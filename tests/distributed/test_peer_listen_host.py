"""Tests for the worker-advertised peer_listen_host.

The chain protocol forwards hidden state directly between workers via
TCP. The hub tells worker A "to reach worker B, connect to host:port."
That `host` used to be whatever IP B connected to the hub from — fine
for single-machine pools where everyone's at 127.0.0.1, but broken
cross-machine: a worker on the same machine as the hub connects via
loopback, so the hub records `127.0.0.1`, then a remote worker is
told to "reach the local worker at 127.0.0.1" — which from its view
points back at itself.

Workers now pick their own routable IP (Tailscale CGNAT preferred,
then private LAN, then loopback as last resort) and send it in HELLO
as `peer_listen_host`. These tests pin that selection logic.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from dots.hub import Participant
from dots.util import best_peer_listen_host


class _StubConn:
    async def call(self, *a, **k):
        return {}

    async def notify(self, *a, **k):
        return None

    async def close(self):
        return None


def test_participant_peer_host_uses_advertised_when_present():
    """When the worker sent `peer_listen_host` in HELLO, the hub uses
    THAT for chain payloads — not the connect-from IP."""
    p = Participant(
        id="w1",
        addr="127.0.0.1:54000",
        conn=_StubConn(),
        peer_listen_port=50001,
        peer_listen_host="100.106.22.71",  # worker's Tailscale IP
    )
    assert p.peer_host == "100.106.22.71", (
        "advertised peer_listen_host should win over connect-from IP"
    )


def test_participant_peer_host_requires_advertised_address():
    """The hub must not infer a peer address from the control connection."""
    p = Participant(
        id="w1",
        addr="127.0.0.1:54000",
        conn=_StubConn(),
        peer_listen_port=50001,
        peer_listen_host=None,
    )
    with pytest.raises(RuntimeError, match="peer_listen_host"):
        _ = p.peer_host


def test_best_peer_listen_host_prefers_tailscale():
    """Tailscale CGNAT (100.64.0.0/10) wins over private-LAN, which
    wins over loopback. Dots is designed around tailnet routing so
    that's the right default."""
    fake_ifconfig = b"""
lo0: flags=8049
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863
\tinet 10.30.142.233 netmask 0xffffff00
utun4: flags=8051
\tinet 100.106.22.71 netmask 0xffffffff
"""

    def fake_getaddrinfo(*_a, **_kw):
        return []  # rely on the ifconfig path

    def fake_check_output(*_a, **_kw):
        return fake_ifconfig

    with (
        patch("socket.getaddrinfo", side_effect=fake_getaddrinfo),
        patch("subprocess.check_output", side_effect=fake_check_output),
    ):
        assert best_peer_listen_host() == "100.106.22.71"


def test_best_peer_listen_host_falls_back_to_lan_without_tailscale():
    """No Tailscale interface? Use the LAN IP instead of loopback."""
    fake_ifconfig = b"""
lo0: flags=8049
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863
\tinet 192.168.1.42 netmask 0xffffff00
"""

    def fake_getaddrinfo(*_a, **_kw):
        return []

    def fake_check_output(*_a, **_kw):
        return fake_ifconfig

    with (
        patch("socket.getaddrinfo", side_effect=fake_getaddrinfo),
        patch("subprocess.check_output", side_effect=fake_check_output),
    ):
        assert best_peer_listen_host() == "192.168.1.42"


def test_best_peer_listen_host_falls_back_to_loopback_when_nothing_else():
    """Single-machine pool with only loopback bound — that's still
    correct, returns 127.0.0.1."""
    fake_ifconfig = b"""
lo0: flags=8049
\tinet 127.0.0.1 netmask 0xff000000
"""

    def fake_getaddrinfo(*_a, **_kw):
        return []

    def fake_check_output(*_a, **_kw):
        return fake_ifconfig

    with (
        patch("socket.getaddrinfo", side_effect=fake_getaddrinfo),
        patch("subprocess.check_output", side_effect=fake_check_output),
    ):
        assert best_peer_listen_host() == "127.0.0.1"

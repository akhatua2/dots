"""Where a pass of the model goes when its layers span Macs: here, on each peer, and on the wire.

The head worker times every pass of a turn: its own layers (with turning the hidden state into bytes, which
is when MLX actually computes them), then each hop's round trip, of which the peer reports how much it spent
computing. The rest of a round trip is the network and the copies on either end.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChainTiming:
    peers: int
    # A ring times the whole loop as one hop: the hidden state goes peer to peer and comes home once
    ring: bool = False
    passes: int = 0
    here_ms: float = 0.0
    round_trip_ms: list[float] = field(default_factory=list)
    peer_compute_ms: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.round_trip_ms = [0.0] * self.peers
        self.peer_compute_ms = [0.0] * self.peers

    def stats(self) -> dict:
        """Per-pass averages, in milliseconds, for the turn's stats."""
        if not self.passes:
            return {}
        n = self.passes
        return {
            "chain": "ring" if self.ring else "star",
            "chain_passes": n,
            "here_ms": round(self.here_ms / n, 2),
            "hops": [
                {"round_trip_ms": round(rt / n, 2), "compute_ms": round(c / n, 2), "wire_ms": round((rt - c) / n, 2)}
                for rt, c in zip(self.round_trip_ms, self.peer_compute_ms)
            ],
        }


def describe(stats: dict) -> str:
    """' · a pass: here 3.1 ms, hop 1 12.0 ms (8.2 computing)' for the hub's log; empty without a chain."""
    hops = stats.get("hops")
    if not hops:
        return ""
    if stats.get("chain") == "ring":
        ring = hops[0]
        parts = [f"here {stats['here_ms']:.1f} ms",
                 f"ring {ring['round_trip_ms']:.1f} ms ({ring['compute_ms']:.1f} computing)"]
    else:
        parts = [f"here {stats['here_ms']:.1f} ms"] + [
            f"hop {i} {h['round_trip_ms']:.1f} ms ({h['compute_ms']:.1f} computing)" for i, h in enumerate(hops, 1)
        ]
    return " · a pass: " + ", ".join(parts)

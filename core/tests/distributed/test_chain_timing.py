"""A pass across Macs splits into this Mac's time, each peer's compute, and the wire in between."""

from dots.distributed.chain_timing import ChainTiming, describe


def test_per_pass_averages_and_the_wire_is_what_the_peer_did_not_spend_computing():
    timing = ChainTiming(peers=2)
    for _ in range(4):
        timing.passes += 1
        timing.here_ms += 3.0
        timing.round_trip_ms[0] += 12.0
        timing.peer_compute_ms[0] += 8.0
        timing.round_trip_ms[1] += 30.0
        timing.peer_compute_ms[1] += 5.0
    stats = timing.stats()
    assert stats["chain_passes"] == 4 and stats["here_ms"] == 3.0
    assert stats["hops"] == [{"round_trip_ms": 12.0, "compute_ms": 8.0, "wire_ms": 4.0},
                             {"round_trip_ms": 30.0, "compute_ms": 5.0, "wire_ms": 25.0}]
    assert describe(stats) == " · a pass: here 3.0 ms, hop 1 12.0 ms (8.0 computing), hop 2 30.0 ms (5.0 computing)"


def test_a_turn_on_one_mac_says_nothing():
    assert ChainTiming(peers=1).stats() == {}
    assert describe({}) == ""

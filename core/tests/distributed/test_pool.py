"""Unit tests for `dots.pool`."""

from __future__ import annotations

import time

from dots.distributed.pool import (
    Pool,
    resource_decl_from_hello,
    resource_decl_to_hello_field,
)
from tests.helpers.resources import resource_decl


# ───────────────── membership ─────────────────


def test_add_and_remove():
    pool = Pool()
    pool.add_or_update("W1", resource_decl("W1"))
    pool.add_or_update("W2", resource_decl("W2"))
    assert set(pool.known_workers()) == {"W1", "W2"}
    pool.remove("W1")
    assert pool.known_workers() == ["W2"]


def test_remove_clears_latency_cells():
    pool = Pool()
    pool.add_or_update("W1", resource_decl("W1"))
    pool.add_or_update("W2", resource_decl("W2"))
    pool.record_probe("W1", "W2", 5.0)
    assert pool.get_rtt("W1", "W2") == 5.0
    pool.remove("W1")
    assert pool.get_rtt("W1", "W2") is None  # no W1 anymore


# ───────────────── latency ─────────────────


def test_record_probe_symmetric():
    pool = Pool()
    pool.record_probe("W1", "W2", 3.0)
    assert pool.get_rtt("W1", "W2") == 3.0
    assert pool.get_rtt("W2", "W1") == 3.0  # same cell


def test_self_rtt_is_zero():
    pool = Pool()
    assert pool.get_rtt("W1", "W1") == 0.0


def test_unmeasured_rtt_is_none():
    pool = Pool()
    assert pool.get_rtt("W1", "W2") is None


def test_stale_pairs_uses_age():
    pool = Pool()
    old = time.time() - 600
    fresh = time.time() - 10
    pool.record_probe("W1", "W2", 1.0, now_unix=old)
    pool.record_probe("W1", "W3", 2.0, now_unix=fresh)
    stale = pool.stale_pairs(max_age_seconds=120)
    assert ("W1", "W2") in stale
    assert ("W1", "W3") not in stale


def test_missing_pairs():
    pool = Pool()
    for w in ("W1", "W2", "W3"):
        pool.add_or_update(w, resource_decl(w))
    pool.record_probe("W1", "W2", 1.0)
    missing = pool.missing_pairs()
    assert ("W1", "W3") in missing
    assert ("W2", "W3") in missing
    assert ("W1", "W2") not in missing


# ───────────────── scheduler hand-off ─────────────────


def test_snapshot_includes_default_for_unmeasured():
    """Pairs with no probe yet get a 5ms default so they aren't treated as
    infeasible. Otherwise the very first query (before any probe) would
    always fail."""
    pool = Pool()
    pool.add_or_update("W1", resource_decl("W1"))
    pool.add_or_update("W2", resource_decl("W2"))
    _, latency = pool.snapshot()
    assert latency.reachable("W1", "W2")
    assert 0 < latency.get("W1", "W2") < 100


def test_snapshot_uses_measured_when_present():
    pool = Pool()
    pool.add_or_update("W1", resource_decl("W1"))
    pool.add_or_update("W2", resource_decl("W2"))
    pool.record_probe("W1", "W2", 42.5)
    _, latency = pool.snapshot()
    assert latency.get("W1", "W2") == 42.5


# ───────────────── ResourceDecl from HELLO ─────────────────


def test_resource_decl_from_modern_hello():
    hello = {
        "account_id": "acc-x",
        "resources": {
            "ram_gb_free": 18.0,
            "compute_tflops_per_sec": 1.5,
            "backends": ["mlx"],
            "cluster_id": "A",
            "disk_gb_free": 200.0,
            "cpu_utilization": 0.25,
            "io_read_gbps": 2.0,
            "io_write_gbps": 1.0,
        },
    }
    decl = resource_decl_from_hello(hello, worker_id="W1")
    assert decl is not None
    assert decl.ram_gb_free == 18.0
    assert decl.compute_tflops_per_sec == 1.5
    assert decl.backends == frozenset({"mlx"})
    assert decl.cluster_id == "A"
    assert decl.disk_gb_free == 200.0
    assert decl.cpu_utilization == 0.25
    assert decl.io_read_gbps == 2.0
    assert decl.io_write_gbps == 1.0


def test_resource_decl_without_resources_is_none():
    hello = {"account_id": "acc-y", "free_ram_gb": 8.0}
    assert resource_decl_from_hello(hello, worker_id="W2") is None


def test_resource_decl_from_empty_hello_is_none():
    assert resource_decl_from_hello({}, worker_id="W3") is None


def test_hello_field_roundtrip():
    decl = resource_decl("W1", ram_gb=22.0)
    field = resource_decl_to_hello_field(decl)
    restored = resource_decl_from_hello(
        {"resources": field, "account_id": decl.account_id}, "W1"
    )
    assert restored == decl

"""Coarse geo helper: opt-out, validation, and coarsening."""

from dots.system.geo import lookup_coarse_geo, sanitize_geo


def test_opt_out_returns_none(monkeypatch):
    monkeypatch.setenv("DOTS_NO_GEO", "1")
    assert lookup_coarse_geo() is None


def test_sanitize_rejects_non_dict():
    assert sanitize_geo(None) is None
    assert sanitize_geo("nope") is None
    assert sanitize_geo(42) is None


def test_sanitize_rejects_missing_or_bad_coords():
    assert sanitize_geo({}) is None
    assert sanitize_geo({"lat": "x", "lng": 1}) is None
    assert sanitize_geo({"lat": 91, "lng": 0}) is None  # out of range
    assert sanitize_geo({"lat": 0, "lng": 181}) is None


def test_sanitize_coarsens_and_keeps_fields():
    out = sanitize_geo(
        {
            "ip": "203.0.113.7",
            "lat": 37.7749295,
            "lng": -122.4194155,
            "city": "San Francisco",
            "country": "United States",
        }
    )
    assert out == {
        "ip": "203.0.113.7",
        "lat": 37.8,  # rounded to 1 decimal (~11 km)
        "lng": -122.4,
        "city": "San Francisco",
        "country": "United States",
    }


def test_sanitize_drops_blank_optional_fields():
    out = sanitize_geo({"lat": 1.234, "lng": 5.678, "ip": "", "city": "", "country": None})
    assert out == {"ip": None, "lat": 1.2, "lng": 5.7, "city": None, "country": None}

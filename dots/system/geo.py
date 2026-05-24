"""Best-effort coarse geolocation for the worker map.

A worker optionally looks up its own approximate location (city-level) from its
public IP and advertises ``{lat, lng, city, country}`` in its HELLO, so the hub
can render a map of where the pool is. Properties:

  * **Opt-out** — set ``DOTS_NO_GEO=1`` and nothing is looked up or sent.
  * **Best-effort** — any failure (offline, slow, blocked) → ``None``; never
    blocks the worker from joining.
  * **Coarse** — coordinates are rounded to ~city granularity (1 decimal,
    ≈ 11 km) so this never pins a precise address.
  * **Worker-controlled** — the hub never sees the worker's IP; it only sees
    the rounded coordinates the worker chooses to send.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request

log = logging.getLogger("dots.geo")

# Free, no-key IP geolocation. http-only on the free tier, which is fine for a
# server-side worker process. `query` echoes back the public IP we came from.
_GEO_URL = "http://ip-api.com/json/?fields=status,query,country,city,lat,lon"


def lookup_coarse_geo(timeout: float = 2.0) -> dict | None:
    """Return this machine's ``{ip, lat, lng, city, country}`` or ``None``."""
    if os.environ.get("DOTS_NO_GEO"):
        return None
    try:
        req = urllib.request.Request(_GEO_URL, headers={"User-Agent": "dots-worker"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # offline / blocked / slow — never fatal
        log.debug("geo lookup failed: %r", e)
        return None
    if data.get("status") != "success":
        return None
    return sanitize_geo(
        {
            "ip": data.get("query"),
            "lat": data.get("lat"),
            "lng": data.get("lon"),
            "city": data.get("city"),
            "country": data.get("country"),
        }
    )


def sanitize_geo(g: object) -> dict | None:
    """Validate + coarsen an advertised geo blob; drop anything malformed.

    Used both by the worker (on its own lookup) and the hub (on the value a
    worker advertised) so neither side trusts unbounded input.
    """
    if not isinstance(g, dict):
        return None
    lat, lng = g.get("lat"), g.get("lng")
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None
    city = g.get("city")
    country = g.get("country")
    ip = g.get("ip")
    return {
        "ip": ip if isinstance(ip, str) and ip else None,
        "lat": round(float(lat), 1),  # ~11 km — coarse on purpose
        "lng": round(float(lng), 1),
        "city": city if isinstance(city, str) and city else None,
        "country": country if isinstance(country, str) and country else None,
    }

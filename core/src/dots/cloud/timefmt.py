"""Dates on the wire: ISO 8601 to the second, in UTC. The Mac app's decoder rejects fractional seconds."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def night_of(when: datetime) -> datetime:
    """Nights run noon to noon, local time. A night is named by the noon it began, given in UTC."""
    local = when.astimezone()
    noon = local.replace(hour=12, minute=0, second=0, microsecond=0)
    if local < noon:
        noon -= timedelta(days=1)
    return noon.astimezone(timezone.utc)

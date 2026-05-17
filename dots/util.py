"""Shared utilities."""
from __future__ import annotations

import os
import pathlib
import subprocess
import uuid


def free_ram_gb() -> float:
    """Approximate free RAM on macOS in gigabytes (free + inactive pages)."""
    out = subprocess.check_output(["vm_stat"]).decode()
    pages_free = pages_inactive = 0
    for line in out.splitlines():
        if line.startswith("Pages free:"):
            pages_free = int(line.split()[-1].rstrip("."))
        elif line.startswith("Pages inactive:"):
            pages_inactive = int(line.split()[-1].rstrip("."))
    return (pages_free + pages_inactive) * 16384 / (1024**3)


def dots_home() -> pathlib.Path:
    """Where we keep per-user state (identity, future config). Overridable
    via $DOTS_HOME for tests and multi-worker-per-machine setups."""
    override = os.environ.get("DOTS_HOME")
    return pathlib.Path(override) if override else pathlib.Path.home() / ".dots"


def load_or_create_identity() -> str:
    """Return this machine's stable worker UUID. Generated on first call and
    persisted to {dots_home}/identity so it survives reboots and reconnects.
    Once issued, the hub uses it to recognize a returning worker — opens the
    door to per-worker preferences, auth, and contribution stats later."""
    home = dots_home()
    home.mkdir(parents=True, exist_ok=True)
    f = home / "identity"
    if f.exists():
        existing = f.read_text().strip()
        if existing:
            return existing
    new_id = str(uuid.uuid4())
    f.write_text(new_id + "\n")
    return new_id


def save_last_hub(addr: str) -> None:
    """Persist the most recently successful hub address so the user can
    run `dots chat` or `dots worker` without args next time."""
    home = dots_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "last_hub").write_text(addr.strip() + "\n")


def load_last_hub() -> str | None:
    f = dots_home() / "last_hub"
    if not f.exists():
        return None
    addr = f.read_text().strip()
    return addr or None

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


def worker_pid_file() -> pathlib.Path:
    return dots_home() / "worker.pid"


def write_worker_pid(pid: int) -> None:
    """Record a long-running `dots worker`'s PID so `dots chat` can detect
    it and skip spawning a duplicate."""
    home = dots_home()
    home.mkdir(parents=True, exist_ok=True)
    worker_pid_file().write_text(f"{pid}\n")


def clear_worker_pid(only_if: int | None = None) -> None:
    """Remove the worker PID file. If only_if is given, only delete when
    the file points at exactly that pid (so we don't trample a successor
    worker that started between our write and our cleanup)."""
    f = worker_pid_file()
    if not f.exists():
        return
    if only_if is not None:
        try:
            current = int(f.read_text().strip())
        except (ValueError, OSError):
            return
        if current != only_if:
            return
    try:
        f.unlink()
    except OSError:
        pass


def read_live_worker_pid() -> int | None:
    """Returns the PID of a `dots worker` running on this machine, or None
    if no PID is recorded or the recorded process is dead."""
    f = worker_pid_file()
    if not f.exists():
        return None
    try:
        pid = int(f.read_text().strip())
    except (ValueError, OSError):
        return None
    try:
        os.kill(pid, 0)        # signal 0 = liveness probe, no actual signal
    except OSError:
        # stale entry — process is gone
        try:
            f.unlink()
        except OSError:
            pass
        return None
    return pid

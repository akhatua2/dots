"""Shared utilities."""
from __future__ import annotations

import hashlib
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


def cpu_brand_string() -> str:
    """Return the machine's CPU brand string (e.g. 'Apple M2 Pro').

    Empty string on non-macOS or if sysctl is unavailable.
    """
    try:
        out = subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return ""


# Rough effective TFLOPs for 4-bit MLX decode workloads (memory-bandwidth
# limited, not peak compute). Values calibrated against public mlx-lm
# benchmark reports. Treat as ±20% accurate; refine when we have real
# workload telemetry that the scheduler can learn from.
_CHIP_THROUGHPUT_TFLOPS: tuple[tuple[str, float], ...] = (
    # Order matters: more specific strings come first.
    ("m2 ultra", 3.5),
    ("m3 max",   2.5),
    ("m1 max",   1.5),
    ("m2 max",   2.0),
    ("m3 pro",   1.8),
    ("m1 pro",   0.8),
    ("m2 pro",   1.2),
    ("m3",       1.2),
    ("m2",       0.5),
    ("m1",       0.4),
)


def effective_tflops_estimate(brand: str | None = None) -> float:
    """Best-effort effective TFLOPs estimate for 4-bit LLM inference.

    Workers report this in HELLO so the hub's scheduler can predict TPS
    for placements. Override via $DOTS_TFLOPS for explicit configuration.
    """
    override = os.environ.get("DOTS_TFLOPS")
    if override is not None:
        try:
            return float(override)
        except ValueError:
            pass
    brand = brand if brand is not None else cpu_brand_string()
    low = brand.lower()
    for needle, tflops in _CHIP_THROUGHPUT_TFLOPS:
        if needle in low:
            return tflops
    return 1.0  # generic fallback


_BACKEND_OVERRIDE_ENV = "DOTS_BACKENDS"


def supported_backends() -> frozenset[str]:
    """Backends this worker can run on. Reported in HELLO.

    v1: always {"mlx"} on Apple Silicon; respected $DOTS_BACKENDS env var
    for testing other backends (comma-separated).
    """
    override = os.environ.get(_BACKEND_OVERRIDE_ENV)
    if override:
        return frozenset(s.strip() for s in override.split(",") if s.strip())
    return frozenset({"mlx"})


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


def load_or_create_account() -> str:
    """Return the human-owner's account ID. Auto-generates a random one if
    no account file exists; users can run `dots account create` for an
    interactive setup that supports password-derived recovery."""
    home = dots_home()
    home.mkdir(parents=True, exist_ok=True)
    f = home / "account"
    if f.exists():
        existing = f.read_text().strip()
        if existing:
            return existing
    new_id = "acc-" + uuid.uuid4().hex
    f.write_text(new_id + "\n")
    return new_id


def save_account_id(account_id: str) -> None:
    """Write account_id to {dots_home}/account. Used by `dots account create`."""
    home = dots_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "account").write_text(account_id.strip() + "\n")


def derive_account_id(name: str, email: str, password: str) -> str:
    """Deterministically derive an account_id from (name, email, password).

    The same three inputs always produce the same account_id. That's the
    recovery property: if you lose your `~/.dots/account` file, re-running
    `dots account create` with the same inputs reproduces the same id and
    your credit ledger history is preserved.

    Uses PBKDF2-HMAC-SHA256 with 100k iterations to slow brute-force
    attacks against weak passwords. Salt is a fixed project constant
    (not secret — it only namespaces this derivation against unrelated
    KDF uses elsewhere).
    """
    salt = b"dots-account-v1"
    name_n = name.lower().strip()
    email_n = email.lower().strip()
    material = f"{name_n}|{email_n}|{password}".encode("utf-8")
    h = hashlib.pbkdf2_hmac("sha256", material, salt, iterations=100_000)
    return "acc-" + h.hex()[:32]


def account_id_short(account_id: str) -> str:
    """Short, log-friendly form. Strips the 'acc-' prefix if present."""
    s = account_id[4:] if account_id.startswith("acc-") else account_id
    return s[:8] if s else "?"


def _read_account_field(name: str) -> str | None:
    f = dots_home() / name
    if not f.exists():
        return None
    val = f.read_text().strip()
    return val or None


def _write_account_field(name: str, value: str) -> None:
    home = dots_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / name).write_text(value.strip() + "\n")


def load_account_alias() -> str | None:
    """Cosmetic short handle for this account (e.g. 'arpan'). Sent on HELLO
    so other pool members see a name instead of a UUID."""
    return _read_account_field("account-alias")


def save_account_alias(alias: str) -> None:
    _write_account_field("account-alias", alias)


def load_account_name() -> str | None:
    """Full human-readable name. Stays local; not sent on HELLO unless the
    user opts in (future). Useful for self-display."""
    return _read_account_field("account-name")


def save_account_name(name: str) -> None:
    _write_account_field("account-name", name)


def load_account_email() -> str | None:
    """Contact email. Stays local; not sent on HELLO. Useful for the user
    to remember which email they tied this account to."""
    return _read_account_field("account-email")


def save_account_email(email: str) -> None:
    _write_account_field("account-email", email)


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
    """Returns the PID of a *real* `dots worker` running on this machine,
    or None if no PID is recorded, the recorded process is dead, or the
    PID got recycled to an unrelated program. We confirm by reading the
    process's command line; if it doesn't look like a dots worker, we
    treat the file as stale and clear it."""
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
        try:
            f.unlink()
        except OSError:
            pass
        return None
    # Verify it's actually a dots worker by inspecting the command line.
    # `ps -p PID -o command=` returns the cmdline minus the PID/header.
    try:
        out = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            stderr=subprocess.DEVNULL, text=True,
        )
        if "dots" not in out or "worker" not in out:
            # Recycled to an unrelated program — drop the stale file.
            try:
                f.unlink()
            except OSError:
                pass
            return None
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return pid

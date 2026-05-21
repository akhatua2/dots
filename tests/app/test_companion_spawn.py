"""Tests for the auto-spawned companion `dots worker` subprocess.

The chat client spawns a companion worker subprocess so the user
contributes compute while they chat. If the argv we build doesn't
parse through dots' own argparse, the subprocess dies immediately on
argparse error (exit code 2), the hub never sees a worker join, and
the chat sits forever on "Waiting for more machines".

This happened once: `--verbose` was put AFTER the `worker` subcommand,
which argparse rejected (it's a top-level flag). Pin that here so the
regression can't sneak back in.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from dots.cli import _companion_worker_argv


REPO_ROOT = Path(__file__).resolve().parent.parent


def _runs_through_argparse(argv: list[str]) -> tuple[int, str]:
    """Invoke `python -m dots.cli ... --help` with the assembled argv
    and report the exit code + stderr. We use --help so the subprocess
    exits cleanly (exit 0) on a successful parse, instead of trying to
    actually connect to a hub.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "dots.cli", *argv, "--help"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=15,
    )
    return proc.returncode, proc.stderr


def test_companion_argv_default_parses():
    """`dots chat HUB:PORT` spawns the companion without --max-ram."""
    argv = _companion_worker_argv("127.0.0.1:5555")
    rc, err = _runs_through_argparse(argv)
    assert rc == 0, (
        f"companion argv {argv!r} failed to parse (exit {rc}); stderr:\n{err}"
    )


def test_companion_argv_with_max_ram_parses():
    """`dots chat HUB:PORT --max-ram 5` propagates the cap to the
    companion worker."""
    argv = _companion_worker_argv("127.0.0.1:5555", max_ram_gb=5.0)
    rc, err = _runs_through_argparse(argv)
    assert rc == 0, (
        f"companion argv {argv!r} failed to parse (exit {rc}); stderr:\n{err}"
    )
    assert "--max-ram" in argv
    assert "5.0" in argv


def test_verbose_is_before_subcommand():
    """`--verbose` is a top-level flag — argparse rejects it after
    the subcommand. Pin the ordering here so a future refactor that
    appends `--verbose` to the end can't break companion spawning."""
    argv = _companion_worker_argv("127.0.0.1:5555")
    verbose_idx = argv.index("--verbose")
    worker_idx = argv.index("worker")
    assert verbose_idx < worker_idx, (
        f"--verbose must precede the `worker` subcommand; got argv={argv}"
    )


def test_companion_subprocess_actually_starts():
    """End-to-end: spawn the companion process the same way `dots chat`
    does, but kill it before it tries to load a model. If argparse fails,
    the process exits in ~50ms with code 2 — the timeout-driven kill
    won't reach it. So the test passes if the process is still alive
    after a short delay (i.e. it got past argparse and is doing real
    startup work)."""
    import time

    argv = _companion_worker_argv("127.0.0.1:65535")  # nothing listens here
    proc = subprocess.Popen(
        [sys.executable, "-m", "dots.cli", *argv],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        time.sleep(1.5)  # give it more than enough time to die on argparse
        argparse_died = proc.poll() == 2
        assert not argparse_died, (
            "companion subprocess died with argparse error code 2 — "
            "the assembled argv was rejected by dots' own parser"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

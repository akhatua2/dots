"""MLX environment checks for opt-in real-model tests."""

from __future__ import annotations

import subprocess
import sys


def mlx_available_reason() -> str | None:
    """Return None when MLX can initialize, otherwise a skip reason."""
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import mlx.core as mx; mx.array([0]).tolist()",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout).strip().splitlines()
        reason = detail[-1] if detail else "probe failed"
        return f"MLX/Metal unavailable: {reason}"
    return None

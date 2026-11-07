"""Backend runtime registry."""

from __future__ import annotations

import functools
import subprocess
import sys

from .base import BackendRuntime

# Backends known to the architecture. Some are scaffolds so placement and
# capability contracts can be designed without pretending they execute yet.
SUPPORTED_BACKENDS = ("mlx", "llama_cpp")
# Backends this build can actually execute. Workers advertise this list by
# default; the hub still requires an explicit selected backend per assignment.
IMPLEMENTED_BACKENDS = ("mlx",)


def get_backend_runtime(name: str) -> BackendRuntime:
    selected = name.strip()
    if not selected:
        raise RuntimeError("backend name is required")
    if selected == "mlx":
        from .mlx.runtime import MlxBackendRuntime

        return MlxBackendRuntime()
    if selected in {"llama_cpp", "cpu"}:
        from .llama_cpp import LlamaCppBackendRuntime

        return LlamaCppBackendRuntime()
    raise RuntimeError(
        f"backend not implemented: {selected!r}. Supported backends: mlx, llama_cpp"
    )


def supported_backend_names() -> frozenset[str]:
    return frozenset(IMPLEMENTED_BACKENDS)


def available_backend_names() -> frozenset[str]:
    """Implemented backends that are usable on this worker process."""
    return frozenset(
        name for name in IMPLEMENTED_BACKENDS if is_backend_available(name)
    )


def is_backend_available(name: str) -> bool:
    selected = name.strip()
    if selected == "mlx":
        return _mlx_available()
    return False


@functools.lru_cache(maxsize=1)
def _mlx_available() -> bool:
    if getattr(sys, "frozen", False):
        # A packaged worker (the one inside the Mac app) is its own interpreter, with no Python to probe with
        try:
            import mlx.core as mx

            return bool(mx.metal.is_available())
        except Exception:
            return False
    probe = (
        "import mlx.core as mx; "
        "import sys; "
        "sys.exit(0 if mx.metal.is_available() else 2)"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0

"""Spinner + progress-bar rendering for the chat client.

The pre-flight phase (before the input bar appears) and the mid-session
not-ready states both reuse the same set of glyphs. Centralizing them
here keeps the chat UI free of magic strings and makes the styling
trivial to tweak in one place.
"""

from __future__ import annotations

# Star-burst rotation, matches Claude Code's loading style. Grows
# from a small dot through several star shapes for a breathing effect
# instead of a strict mechanical spin.
SPINNER_FRAMES: tuple[str, ...] = (
    "·",
    "✦",
    "✢",
    "✻",
    "✺",
    "✽",
    "✶",
)


# User-facing labels for the non-ready pool states the hub distinguishes.
# Plain language; no numbers, no model ids.
#
#   loading        — this machine is still downloading the model.
#   loading_peers  — this machine is done; waiting for the other
#                    machines in the pool to finish their downloads.
#   waiting        — the pool can't be assembled yet (no compute workers
#                    or not enough RAM to fit the model).
POOL_STATE_TEXT: dict[str, str] = {
    "loading": "Loading the model",
    "loading_peers": "Waiting for other machines",
    "waiting": "Waiting for more machines",
}


# Width of the progress bar in cells. 40 matches Claude Code's bar.
BAR_WIDTH = 40
FILLED = "▰"
EMPTY = "▱"


def render_bar(progress: float, width: int = BAR_WIDTH) -> str:
    """Return a `▰▰▱▱…  42%` style bar for `progress ∈ [0, 1]`."""
    progress = max(0.0, min(1.0, progress))
    filled = int(round(progress * width))
    return FILLED * filled + EMPTY * (width - filled) + f"  {progress * 100:.0f}%"


def fmt_bytes(n: int) -> str:
    """Human-readable byte count: `0 B`, `912 KB`, `1.8 GB`."""
    n_f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n_f < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(n_f)} B"
            return f"{n_f:.1f} {unit}"
        n_f /= 1024
    return f"{n_f:.1f} GB"


def frame_at(idx: int) -> str:
    """Pick a spinner frame for tick `idx`, wrapping at the end."""
    return SPINNER_FRAMES[idx % len(SPINNER_FRAMES)]

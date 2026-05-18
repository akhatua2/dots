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
    "·", "✦", "✢", "✻", "✺", "✽", "✶",
)


# User-facing labels for the two non-ready pool states the hub
# distinguishes. Plain language; no numbers, no model ids.
POOL_STATE_TEXT: dict[str, str] = {
    "loading": "Loading the model",
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


def frame_at(idx: int) -> str:
    """Pick a spinner frame for tick `idx`, wrapping at the end."""
    return SPINNER_FRAMES[idx % len(SPINNER_FRAMES)]

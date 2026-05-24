"""Generation strategy registry."""

from __future__ import annotations

import os

from .base import GenerationStrategy

DEFAULT_GENERATION = "autoregressive"
KNOWN_FUTURE_STRATEGIES = ("mtp", "medusa", "prompt_lookup", "eagle")


def get_generation_strategy(name: str | None = None) -> GenerationStrategy:
    selected = (name or os.environ.get("DOTS_GENERATION") or DEFAULT_GENERATION).strip()
    if selected == "autoregressive":
        from .autoregressive import AutoregressiveGeneration

        return AutoregressiveGeneration()
    if selected in KNOWN_FUTURE_STRATEGIES:
        raise RuntimeError(
            f"generation strategy not implemented: {selected!r}. "
            "Supported generation strategy: autoregressive"
        )
    raise RuntimeError(
        f"generation strategy not implemented: {selected!r}. "
        "Supported generation strategy: autoregressive"
    )

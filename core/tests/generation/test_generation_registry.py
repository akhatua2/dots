from __future__ import annotations

import pytest

from dots.generation.autoregressive import AutoregressiveGeneration
from dots.generation.registry import get_generation_strategy


def test_generation_defaults_to_autoregressive(monkeypatch):
    monkeypatch.delenv("DOTS_GENERATION", raising=False)
    assert isinstance(get_generation_strategy(), AutoregressiveGeneration)


def test_generation_env_selects_autoregressive(monkeypatch):
    monkeypatch.setenv("DOTS_GENERATION", "autoregressive")
    assert isinstance(get_generation_strategy(), AutoregressiveGeneration)


def test_future_generation_strategy_fails_clearly():
    with pytest.raises(RuntimeError, match="generation strategy not implemented"):
        get_generation_strategy("medusa")

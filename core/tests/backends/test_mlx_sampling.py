"""Repetition-penalty sampling for the mlx backend.

Small/quantized models fall into repetition loops (the same phrase
emitted until max_tokens). `_apply_repetition_penalty` applies an
HF-style penalty to recently generated tokens so their probability
drops. These tests pin the penalty math and its effect on the argmax
winner directly — the autouse `mock_model` fixture fakes the sampler
entry points (`argmax_token`/`_sample`), so we exercise the penalty
function against real `mx.argmax` instead of routing through them.
"""

from __future__ import annotations

import pytest

# mlx is Apple-only; skip this whole module where it isn't installed (e.g. the
# Linux CI test gate). Mac dev machines run it normally.
pytest.importorskip("mlx")

import mlx.core as mx
from dots.backends.mlx.runtime import _apply_repetition_penalty, penalized


def test_penalty_is_noop_when_disabled_or_no_history():
    # penalty == 1.0 → returns the row untouched (same object).
    row = mx.array([5.0, 4.0, 1.0])
    assert _apply_repetition_penalty(row, [0], 1.0) is row
    # no recent tokens → winner unchanged.
    out = _apply_repetition_penalty(mx.array([5.0, 4.0, 1.0]), [], 2.0)
    assert int(mx.argmax(out).item()) == 0


def test_penalty_steers_argmax_away_from_repeated_token():
    # Token 0 is the natural winner (highest logit)…
    assert int(mx.argmax(mx.array([5.0, 4.0, 1.0])).item()) == 0
    # …but once it's in the recent window, penalizing it (5.0/2.0=2.5)
    # drops it below token 1 (4.0), so the winner becomes 1.
    penalized = _apply_repetition_penalty(mx.array([5.0, 4.0, 1.0]), [0], 2.0)
    assert int(mx.argmax(penalized).item()) == 1


def test_penalty_shrinks_logits_toward_zero_by_sign():
    out = _apply_repetition_penalty(mx.array([4.0, -4.0, 2.0]), [0, 1], 2.0)
    vals = out.tolist()
    assert vals[0] == 2.0  # positive divided
    assert vals[1] == -8.0  # negative multiplied (pushed further down)
    assert vals[2] == 2.0  # untouched


def test_penalty_on_the_gpu_matches():
    """Chained guesses penalize on the GPU, from an array of ids that may repeat; same result."""
    mx.random.seed(1)
    row = mx.random.normal((50,)) * 4
    seen = [3, 7, 7, 12, 3, 49]
    expected = _apply_repetition_penalty(mx.array(row), seen, 1.3)
    got = penalized(row[None], mx.array(seen), 1.3)[0]
    assert mx.allclose(got, expected).item()
    assert penalized(row, mx.array([], dtype=mx.int32), 1.3) is row

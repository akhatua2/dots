"""Streaming detokenization.

Decoding one token at a time splits multi-byte UTF-8 (emoji, CJK) across
token boundaries and emits replacement characters (``�``). The mlx
backend wraps mlx_lm's StreamingDetokenizer, which buffers incomplete
byte sequences and only surfaces complete characters. These tests pin
the wrapper + the per-token fallback deterministically (no real model).
"""

from __future__ import annotations

import pytest

# mlx is Apple-only; skip where it isn't installed (e.g. the Linux PR runner).
pytest.importorskip("mlx")

from dots.backends.base import _PerTokenDetokenizer
from dots.backends.mlx.runtime import _MlxStreamDetokenizer


class _FakeUnderlying:
    """Mimics mlx_lm's StreamingDetokenizer: `text` only grows by
    *complete* characters; a partial multi-byte sequence stays buffered
    (an empty `last_segment`) until it finishes."""

    def __init__(self, per_token_text: list[str]):
        self._segs = per_token_text
        self._i = 0
        self.text = ""
        self.offset = 0

    def add_token(self, _token: int) -> None:
        self.text += self._segs[self._i]
        self._i += 1

    def finalize(self) -> None:
        return None

    @property
    def last_segment(self) -> str:
        seg = self.text[self.offset :]
        self.offset = len(self.text)
        return seg


def test_stream_detokenizer_withholds_incomplete_multibyte_char():
    # Two tokens combine into one character: the first carries only part
    # of the byte sequence (nothing complete yet), the second finishes it.
    d = _MlxStreamDetokenizer(_FakeUnderlying(["", "中"]))
    assert d.add_token(1) == "", "incomplete char must not be emitted"
    assert d.add_token(2) == "中", "completed char surfaces intact"


def test_stream_detokenizer_emits_each_complete_token():
    d = _MlxStreamDetokenizer(_FakeUnderlying(["He", "llo"]))
    out = d.add_token(1) + d.add_token(2) + d.finalize()
    assert out == "Hello"
    assert "�" not in out


class _FakeRuntime:
    def decode_token(self, token: int) -> str:
        return f"<{token}>"


def test_per_token_fallback_decodes_each_token_via_runtime():
    d = _PerTokenDetokenizer(_FakeRuntime())
    assert d.add_token(7) == "<7>"
    assert d.finalize() == ""

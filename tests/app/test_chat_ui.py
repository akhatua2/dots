"""Tests for the interactive chat UI.

The chat is hard to drive end-to-end (prompt_toolkit Application needs
a real terminal), but the individual ChatUI methods that do all the
actual work are pure and testable:

  * `_echo_user_prompt`     — renders the `  › <text>` line with bg fill
  * `_handle_slash_command` — routes /help, /reset, /usage
  * `_print_help`           — emits the help table
  * `_print_usage`          — pulls /v1/credits and renders a table
  * `_stream_turn`          — drives `worker.send_prompt`, accumulates
                              tokens, updates cumulative stats, retries
                              on `PoolNotReadyError`

For each we mock the Worker and pass ChatUI a Console wired to a
StringIO buffer, then assert on the captured output and on the
ChatUI's state. This catches the kinds of regressions the user has
been running into in real chat sessions: double-printed prompts,
broken markdown rendering, missing retries, etc.
"""

from __future__ import annotations

import asyncio
import io
import json
from unittest.mock import patch

import pytest
from rich.console import Console

from dots.chat import ChatUI
from dots.worker import PoolNotReadyError


pytestmark = pytest.mark.asyncio


# ─────────────────────────────── fakes ────────────────────────────────


class _FakeConn:
    """Stand-in for PeerConnection that records `.call` invocations."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def call(self, kind: str, **payload):
        self.calls.append((kind, payload))
        return {"kind": "ok"}


class _FakeWorker:
    """Minimal Worker stand-in: just the attributes + the methods that
    ChatUI touches. Tests configure the stream behavior per-test."""

    def __init__(
        self,
        *,
        stream_chunks: list[str] | None = None,
        decode_tokens: int = 0,
        decode_ms: float = 0.0,
        send_prompt_exception: Exception | None = None,
        exception_on_call: int | None = None,
    ):
        self.hub_host = "127.0.0.1"
        self.hub_port = 5555
        self.account_id = "acc-test"
        self.conn = _FakeConn()
        self._stream_chunks = stream_chunks or []
        self._decode_tokens = decode_tokens
        self._decode_ms = decode_ms
        # If `exception_on_call` is set, raise the exception on that
        # call number (1-indexed); succeed on later calls. Used to
        # simulate transient PoolNotReadyError.
        self._send_prompt_exception = send_prompt_exception
        self._exception_on_call = exception_on_call
        self.send_prompt_call_count = 0
        self.last_max_tokens: int | None = None

    async def send_prompt(
        self,
        text: str,
        max_tokens: int = 128,
        on_token=None,
    ):
        self.send_prompt_call_count += 1
        self.last_max_tokens = max_tokens
        if self._send_prompt_exception is not None and (
            self._exception_on_call is None
            or self.send_prompt_call_count == self._exception_on_call
        ):
            raise self._send_prompt_exception
        for chunk in self._stream_chunks:
            if on_token is not None:
                on_token(chunk)
        return "".join(self._stream_chunks), {
            "decode_tokens": self._decode_tokens,
            "decode_ms": self._decode_ms,
        }

    async def get_pool_status(self):
        return {"ready": True}


def _ui_with_capturing_console(worker) -> tuple[ChatUI, io.StringIO]:
    """Build a ChatUI whose Console writes to a fresh StringIO buffer.
    Tests assert on `buf.getvalue()` to verify what got printed."""
    ui = ChatUI(worker)
    buf = io.StringIO()
    ui._console = Console(
        file=buf,
        force_terminal=True,
        color_system=None,  # no ANSI escapes so assertions stay readable
        width=120,
        legacy_windows=False,
    )
    return ui, buf


# ─────────────────────── echo + slash commands ────────────────────────


async def test_echo_user_prompt_emits_a_single_line_with_marker():
    worker = _FakeWorker()
    ui, buf = _ui_with_capturing_console(worker)
    ui._echo_user_prompt("hello world")
    out = buf.getvalue()
    # Single `  › hello world` line in the output — not duplicated.
    assert out.count("› hello world") == 1, (
        f"expected exactly one echo line; got:\n{out!r}"
    )
    assert out.startswith("  › ")


async def test_echo_user_prompt_pads_to_terminal_width():
    """The bg fill is meant to extend to the right edge; pad with
    spaces in the same line so a renderer that supports bg sees a
    full-width band."""
    worker = _FakeWorker()
    ui, buf = _ui_with_capturing_console(worker)
    ui._console.width = 80
    ui._echo_user_prompt("hi")
    line = buf.getvalue().rstrip("\n")
    # `  › hi` is 6 chars; pad to 80 → length should be >= 78 (rich may
    # collapse trailing space; just verify it pads beyond the message).
    assert len(line) > 50, (
        f"echo wasn't padded; got line of length {len(line)}: {line!r}"
    )


async def test_handle_slash_reset_calls_worker_and_prints_notice():
    worker = _FakeWorker()
    ui, buf = _ui_with_capturing_console(worker)
    handled = await ui._handle_slash_command("/reset")
    assert handled is True
    assert any(kind == "reset" for kind, _ in worker.conn.calls), (
        f"expected /reset to call worker.conn.call('reset'); got {worker.conn.calls}"
    )
    assert "[conversation reset]" in buf.getvalue()


async def test_handle_slash_help_prints_all_commands():
    worker = _FakeWorker()
    ui, buf = _ui_with_capturing_console(worker)
    handled = await ui._handle_slash_command("/help")
    assert handled is True
    out = buf.getvalue()
    for cmd in ("/help", "/reset", "/usage", "Ctrl-D"):
        assert cmd in out, f"/help is missing {cmd}:\n{out}"


async def test_handle_unknown_slash_returns_false():
    """Unrecognized `/whatever` shouldn't be swallowed — it falls
    through and gets sent to the model as a regular prompt."""
    worker = _FakeWorker()
    ui, _ = _ui_with_capturing_console(worker)
    handled = await ui._handle_slash_command("/notacommand")
    assert handled is False


# ────────────────────────── /usage rendering ──────────────────────────


async def test_print_usage_renders_table_from_credits_api():
    """Mocks the HTTP /v1/credits response, verifies the table contains
    every account and that the user's own account shows up first."""
    worker = _FakeWorker()
    ui, buf = _ui_with_capturing_console(worker)
    credits_response = {
        "accounts": {
            "acc-test": {
                "account_id": "acc-test",
                "alias": "me",
                "balance_cu": 0.5,
                "earned_cu": 1.0,
                "spent_cu": 0.5,
                "turns_as_querier": 3,
                "turns_as_worker": 2,
            },
            "acc-other": {
                "account_id": "acc-other",
                "alias": "peer",
                "balance_cu": -0.1,
                "earned_cu": 0.2,
                "spent_cu": 0.3,
                "turns_as_querier": 1,
                "turns_as_worker": 4,
            },
        },
    }

    class _FakeResp:
        def __init__(self, payload):
            self._payload = json.dumps(payload).encode()

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    with patch("urllib.request.urlopen", return_value=_FakeResp(credits_response)):
        await ui._print_usage()

    out = buf.getvalue()
    assert "me" in out, f"own alias missing:\n{out}"
    assert "peer" in out, f"peer alias missing:\n{out}"
    # Self-account should be ordered before the peer.
    assert out.index("me") < out.index("peer"), f"self should sort first:\n{out}"
    # Balance / earned / spent labels show.
    for col in ("balance", "earned", "spent", "turns"):
        assert col in out, f"column {col!r} missing:\n{out}"


async def test_print_usage_handles_network_error_gracefully():
    """If the credits HTTP call fails (offline, hub down on 8080),
    print an error notice instead of crashing the chat task."""
    worker = _FakeWorker()
    ui, buf = _ui_with_capturing_console(worker)
    with patch("urllib.request.urlopen", side_effect=OSError("no network")):
        await ui._print_usage()
    assert "/usage failed" in buf.getvalue()


# ──────────────────────── _stream_turn behavior ───────────────────────


async def test_stream_turn_accumulates_tokens_and_updates_session_stats():
    """The chat shows session-cumulative tps. A single turn should
    bump `_total_decode_tokens` + `_total_decode_s` by the worker's
    reported values."""
    worker = _FakeWorker(
        stream_chunks=["Hello", " world", "!"],
        decode_tokens=12,
        decode_ms=400.0,
    )
    ui, _ = _ui_with_capturing_console(worker)
    assert ui._total_decode_tokens == 0
    assert ui._total_decode_s == 0.0

    await ui._stream_turn("hi")

    assert ui._total_decode_tokens == 12
    assert ui._total_decode_s == pytest.approx(0.4)
    assert worker.send_prompt_call_count == 1


async def test_stream_turn_session_stats_are_cumulative_across_turns():
    """Two turns should add up — average tps is the ratio, the
    individual turns aren't averaged independently."""
    worker = _FakeWorker(
        stream_chunks=["x"],
        decode_tokens=10,
        decode_ms=500.0,
    )
    ui, _ = _ui_with_capturing_console(worker)
    await ui._stream_turn("a")
    await ui._stream_turn("b")
    assert ui._total_decode_tokens == 20
    assert ui._total_decode_s == pytest.approx(1.0)
    # Average tps = 20 tokens / 1.0s = 20.0
    avg = ui._total_decode_tokens / ui._total_decode_s
    assert avg == pytest.approx(20.0)


async def test_stream_turn_passes_max_tokens_2048():
    """The interactive chat caps at 2048 — make sure the value lands
    in worker.send_prompt, not the previous 256 default."""
    worker = _FakeWorker(stream_chunks=["x"], decode_tokens=1, decode_ms=10)
    ui, _ = _ui_with_capturing_console(worker)
    await ui._stream_turn("anything")
    assert worker.last_max_tokens == 2048, (
        f"expected max_tokens=2048; got {worker.last_max_tokens}"
    )


async def test_stream_turn_retries_on_pool_not_ready(monkeypatch):
    """Pool dropping mid-session must not error the turn — chat
    should sleep 2s and retry. Speed up the sleep so the test runs
    in milliseconds, not seconds."""
    real_sleep = asyncio.sleep

    async def fast_sleep(s):
        # Honor sleep(0) for cooperative yields; collapse long sleeps.
        await real_sleep(0 if s >= 1.0 else s)

    monkeypatch.setattr("dots.app.chat.ui.asyncio.sleep", fast_sleep)

    worker = _FakeWorker(
        stream_chunks=["ok"],
        decode_tokens=1,
        decode_ms=10,
        send_prompt_exception=PoolNotReadyError("loading"),
        exception_on_call=1,  # only the first call raises
    )
    ui, _ = _ui_with_capturing_console(worker)
    await asyncio.wait_for(ui._stream_turn("hi"), timeout=2.0)
    assert worker.send_prompt_call_count == 2, (
        f"expected retry after PoolNotReadyError; got "
        f"{worker.send_prompt_call_count} call(s)"
    )
    assert ui._total_decode_tokens == 1


async def test_stream_turn_runtime_error_does_not_crash_chat():
    """A RuntimeError from the worker (e.g., 'turn failed') should be
    printed as a red notice but not propagate — the chat loop has to
    keep running so the user can submit the next prompt."""
    worker = _FakeWorker(
        stream_chunks=[],
        send_prompt_exception=RuntimeError("something broke"),
    )
    ui, buf = _ui_with_capturing_console(worker)
    await ui._stream_turn("hi")  # no exception raised
    assert "something broke" in buf.getvalue()
    # Session stats unchanged on failure.
    assert ui._total_decode_tokens == 0

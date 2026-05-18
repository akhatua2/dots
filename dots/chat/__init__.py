"""Interactive chat UI for dots.

The chat surface is a small prompt_toolkit Application built around a
Worker that already owns the hub connection. The Worker handles compute
(LOAD_SHARD / COMPUTE / RESET); ChatUI handles only what the human types
and sees — input box, history, status row, token streaming, scrolling.

Keeping these two responsibilities in separate files makes it possible
to run them independently: a node that only wants to donate compute
spawns just the Worker (see Worker.run_headless), while a node that
wants to talk to the model wraps the same Worker in a ChatUI and runs
that on top.
"""
from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.styles import Style

from .. import ui

if TYPE_CHECKING:
    from ..worker import Worker


# 2-space leading margin for everything in the chat area. `›` sits at col 2;
# response lines and wrapped continuations start at col 4 so they align under
# the user's text — same visual pattern as Claude Code.
INDENT = "  "


_PROMPT_STYLE = Style.from_dict({
    "prompt": "ansimagenta bold",
    "": "",
    "rule": "fg:#5a5a5a bg:default",
    "banner.star": "fg:ansiyellow bold bg:default",
    "banner.dot": "fg:#7a4b7a bg:default",
    "banner.name": "fg:ansimagenta bold bg:default",
    "banner.version": "fg:#888888 bg:default",
    "banner.key": "fg:ansicyan bg:default",
    "banner.value": "fg:default bg:default",
    "status.marker": "fg:ansimagenta bg:default bold",
    "status.tps": "fg:ansicyan bg:default",
    "status.dim": "fg:#888888 bg:default",
    "status.spinner": "fg:ansicyan bold bg:default",
    "status.notice": "fg:#aaaaaa bg:default",
})


from .spinner import (
    POOL_STATE_TEXT as _POOL_STATE_TEXT,
    SPINNER_FRAMES as _SPINNER_FRAMES,
    fmt_bytes as _fmt_bytes,
    frame_at as _frame_at,
    render_bar as _render_bar,
)


def _ttysize() -> tuple[int, int]:
    return shutil.get_terminal_size((80, 24))


class _Wrapper:
    """Streaming word-wrapper that keeps every emitted visual line at the
    same left margin. Buffers partial words until a space/newline arrives,
    so wrapping happens at real word boundaries even when tokens split
    mid-word (which they routinely do)."""

    def __init__(self, indent: str = INDENT, width: int = 80):
        self.indent = indent
        self.width = max(20, width)
        self._reset()

    def _reset(self) -> None:
        self.cur_col = 0
        self.at_line_start = True
        self.word_buf = ""

    def reset(self) -> None:
        self._reset()

    def set_width(self, width: int) -> None:
        self.width = max(20, width)

    def feed(self, text: str) -> str:
        out: list[str] = []
        for ch in text:
            if ch == "\n":
                self._flush_word(out)
                out.append("\n")
                self.at_line_start = True
                self.cur_col = 0
            elif ch == " " or ch == "\t":
                self._flush_word(out)
                if self.at_line_start:
                    continue
                if self.cur_col + 1 > self.width:
                    out.append("\n")
                    self.at_line_start = True
                    self.cur_col = 0
                else:
                    out.append(ch)
                    self.cur_col += 1
            else:
                self.word_buf += ch
        return "".join(out)

    def _flush_word(self, out: list[str]) -> None:
        if not self.word_buf:
            return
        word, self.word_buf = self.word_buf, ""
        if self.at_line_start:
            out.append(self.indent)
            self.cur_col = len(self.indent)
            self.at_line_start = False
        if (self.cur_col + len(word) > self.width
                and self.cur_col > len(self.indent)):
            out.append("\n")
            out.append(self.indent)
            self.cur_col = len(self.indent)
        out.append(word)
        self.cur_col += len(word)

    def flush(self) -> str:
        out: list[str] = []
        self._flush_word(out)
        return "".join(out)


class ChatUI:
    """Interactive chat surface for a Worker that's already connected to a
    hub. Builds a prompt_toolkit Application: banner on top, scrollable
    conversation history below, and an always-active input box pinned
    underneath. Submitted prompts queue and run FIFO; the user can keep
    typing while a response is streaming."""

    def __init__(self, worker: "Worker"):
        self.worker = worker
        self._app: Application | None = None
        self._latest_tps: float | None = None
        # Pool state shown in the status row when the hub reports
        # `not_ready`. None = serving normally; otherwise one of the
        # values in _POOL_STATE_TEXT.
        self._pool_state: str | None = None
        self._spinner_frame = 0

    async def _wait_for_pool_ready(self, worker: "Worker") -> None:
        """Pre-flight: animate a spinner on stdout until the hub reports
        the pool can serve. Updates in place via `\r`; clears the line
        when ready. Ctrl-C cancels (raises asyncio.CancelledError)."""
        import sys
        # Try once first — if the pool is already serving, skip the
        # spinner entirely (no flicker).
        try:
            status = await worker.get_pool_status()
        except Exception:
            status = {"ready": True}    # fail open; chat surface will show real errors
        if status.get("ready"):
            return
        try:
            frame = 0
            last_msg = ""
            while True:
                state = status.get("state", "loading")
                msg = _POOL_STATE_TEXT.get(state, "Loading")
                spinner = _frame_at(frame)
                # When the hub knows download progress, show a real bar.
                progress = status.get("progress")
                if state == "loading" and isinstance(progress, (int, float)):
                    bytes_now = status.get("bytes_now") or 0
                    bytes_total = status.get("bytes_total") or 0
                    size_suffix = (
                        f"  ·  {_fmt_bytes(bytes_now)} / {_fmt_bytes(bytes_total)}"
                        if bytes_total > 0 else ""
                    )
                    line = (f"  {spinner}  {msg}  "
                            f"{_render_bar(float(progress))}{size_suffix}")
                else:
                    line = f"  {spinner}  {msg}"
                # Pad to clear leftover characters from a longer previous
                # message (waiting → loading is shorter; bar grows).
                pad = max(0, len(last_msg) - len(line))
                sys.stdout.write("\r" + line + (" " * pad))
                sys.stdout.flush()
                last_msg = line
                frame += 1
                await asyncio.sleep(0.18)
                try:
                    status = await worker.get_pool_status()
                except Exception:
                    break
                if status.get("ready"):
                    break
        finally:
            # Clear the spinner line so the chat UI starts clean.
            sys.stdout.write("\r" + " " * (max(len(last_msg), 60)) + "\r")
            sys.stdout.flush()

    async def run(self) -> None:
        worker = self.worker
        # Pre-flight: don't show the input bar until the pool can serve.
        # Animated spinner that updates in place on a single line; clears
        # itself once the hub reports ready.
        await self._wait_for_pool_ready(worker)
        hub_addr = f"{worker.hub_host}:{worker.hub_port}"
        # In the delegation architecture each compute worker hosts the
        # full model, so there's no layer-range banner to show.
        layer_range = None

        history_buf = Buffer()
        input_buf = Buffer(multiline=False)
        wrapper = _Wrapper(indent=INDENT + "  ", width=_ttysize()[0] - 1)
        prompt_queue: asyncio.Queue[str] = asyncio.Queue()
        generating = {"flag": False}

        intro = f"\n{INDENT}/reset to clear conversation · Ctrl-D to exit\n\n"
        history_buf.set_document(Document(intro, cursor_position=len(intro)),
                                 bypass_readonly=True)
        banner_segments = ui.banner_segments(
            worker.model_id, hub_addr, layer_range,
        )

        def append_history(text: str) -> None:
            was_at_end = history_buf.cursor_position == len(history_buf.text)
            new_text = history_buf.text + text
            new_cursor = len(new_text) if was_at_end else history_buf.cursor_position
            history_buf.set_document(
                Document(new_text, cursor_position=new_cursor),
                bypass_readonly=True,
            )
            if self._app is not None:
                self._app.invalidate()

        def top_rule():
            width = max(20, _ttysize()[0] - 1)
            return [("class:rule", INDENT + "─" * (width - len(INDENT)))]

        bottom_rule = top_rule

        def status_text():
            parts: list[tuple[str, str]] = [
                ("class:status.marker", f"{INDENT}‣‣ "),
            ]
            # Pool-state spinner takes precedence over tok/s and
            # "generating" — when the pool isn't ready, the only thing
            # the user wants to know is what we're waiting on.
            if self._pool_state is not None:
                frame = _SPINNER_FRAMES[self._spinner_frame % len(_SPINNER_FRAMES)]
                msg = _POOL_STATE_TEXT.get(self._pool_state, "Loading")
                parts.append(("class:status.spinner", f"{frame} "))
                parts.append(("class:status.notice", f"{msg}  ·  "))
                parts.append(("class:status.dim", "ctrl-c to cancel"))
                return parts
            if self._latest_tps is not None:
                parts.append(("class:status.tps",
                              f"{self._latest_tps:.1f} tok/s"))
                parts.append(("class:status.dim", "  ·  "))
            if generating["flag"]:
                parts.append(("class:status.dim", "generating · "))
            parts.append(("class:status.dim", "ctrl-c to interrupt"))
            return parts

        kb = KeyBindings()

        @kb.add("enter")
        def _on_enter(event):
            text = input_buf.text
            input_buf.reset()
            stripped = text.strip()
            if not stripped:
                return
            prompt_queue.put_nowait(stripped)
            append_history(f"{INDENT}› {stripped}\n")

        @kb.add("c-d")
        def _on_ctrl_d(event):
            event.app.exit()

        @kb.add("c-c")
        def _on_ctrl_c(event):
            event.app.exit()

        # Scroll the history Window by moving the cursor in its buffer;
        # the Window auto-scrolls to keep that cursor visible. Auto-follow
        # resumes naturally when the user scrolls back to the very end.
        def _scroll(rows: int) -> None:
            doc = history_buf.document
            target_row = max(0, doc.cursor_position_row + rows)
            new_pos = doc.translate_row_col_to_index(target_row, 0)
            history_buf.cursor_position = max(0, min(len(history_buf.text), new_pos))

        @kb.add("pageup")
        def _pgup(event):
            _scroll(-10)

        @kb.add("pagedown")
        def _pgdn(event):
            _scroll(10)

        @kb.add("up")
        def _up(event):
            _scroll(-1)

        @kb.add("down")
        def _down(event):
            _scroll(1)

        @kb.add("home")
        def _home(event):
            history_buf.cursor_position = 0

        @kb.add("end")
        def _end(event):
            history_buf.cursor_position = len(history_buf.text)

        layout = Layout(
            HSplit([
                Window(FormattedTextControl(banner_segments),
                       height=4, dont_extend_height=True),
                Window(BufferControl(history_buf, focusable=False),
                       wrap_lines=False, dont_extend_height=True),
                Window(FormattedTextControl(top_rule), height=1),
                Window(
                    BufferControl(
                        input_buf,
                        input_processors=[BeforeInput(
                            f"{INDENT}› ", style="class:prompt",
                        )],
                    ),
                    height=1,
                ),
                Window(FormattedTextControl(bottom_rule), height=1),
                Window(FormattedTextControl(status_text), height=1),
                Window(),
            ]),
            focused_element=input_buf,
        )

        self._app = Application(
            layout=layout,
            key_bindings=kb,
            full_screen=False,
            style=_PROMPT_STYLE,
            mouse_support=False,
        )

        async def chat_processor():
            while True:
                try:
                    user_msg = await prompt_queue.get()
                except asyncio.CancelledError:
                    return
                if user_msg == "/reset":
                    try:
                        await worker.conn.call("reset")
                    except Exception:
                        pass
                    append_history(f"{INDENT}[conversation reset]\n\n")
                    continue
                wrapper.set_width(_ttysize()[0] - 1)
                wrapper.reset()
                generating["flag"] = True
                self._app.invalidate()
                try:
                    # dots.worker is one package level up from dots.chat;
                    # `.worker` would resolve to dots.chat.worker (doesn't
                    # exist), and the resulting ImportError killed the
                    # chat_processor task on the very first prompt,
                    # silently leaving the UI in "ctrl-c to interrupt"
                    # state forever.
                    from ..worker import PoolNotReadyError

                    # If the pool transitions to not_ready mid-session
                    # (e.g. a worker drops and replacement is loading),
                    # show the spinner in the status row instead of
                    # appending to history. Auto-retry every 2s; Ctrl-C
                    # cancels.
                    while True:
                        try:
                            _, resp = await worker.send_prompt(
                                user_msg, max_tokens=256,
                                on_token=lambda t: append_history(wrapper.feed(t)),
                            )
                            self._pool_state = None
                            tail = wrapper.flush()
                            if tail:
                                append_history(tail)
                            decode_tokens = int(resp.get("decode_tokens", 0))
                            decode_s = float(resp.get("decode_ms", 0)) / 1000.0
                            if decode_tokens > 0 and decode_s > 0:
                                self._latest_tps = decode_tokens / decode_s
                            break
                        except PoolNotReadyError as nr:
                            self._pool_state = nr.state
                            if self._app is not None:
                                self._app.invalidate()
                            await asyncio.sleep(2.0)
                except RuntimeError as e:
                    append_history(f"{INDENT}{e}\n")
                except asyncio.CancelledError:
                    self._pool_state = None
                    raise
                finally:
                    self._pool_state = None
                    generating["flag"] = False
                    text = history_buf.text.rstrip("\n") + "\n\n"
                    history_buf.set_document(
                        Document(text, cursor_position=len(text)),
                        bypass_readonly=True,
                    )
                    self._app.invalidate()

        async def spinner_tick() -> None:
            """While `_pool_state` is set, advance the spinner frame and
            invalidate the layout so the status row redraws. Idle when
            the pool is serving normally."""
            while True:
                await asyncio.sleep(0.18)
                if self._pool_state is None:
                    continue
                self._spinner_frame = (self._spinner_frame + 1) % len(_SPINNER_FRAMES)
                if self._app is not None:
                    self._app.invalidate()

        processor_task = asyncio.create_task(chat_processor())
        spinner_task = asyncio.create_task(spinner_tick())
        try:
            await self._app.run_async()
        finally:
            processor_task.cancel()
            spinner_task.cancel()
            for t in (processor_task, spinner_task):
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            self._app = None

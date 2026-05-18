"""Interactive chat UI for dots.

Hybrid layout: prompt_toolkit owns the persistent shell (banner up top,
history pane in the middle, rules + always-on input bar + status row
pinned at the bottom). `rich` owns the *content* rendering — each
assistant response goes through `rich.markdown.Markdown` and is
captured as ANSI-encoded text that the history pane displays.

That way:
  * The input bar is always visible; the user can start typing the
    next message immediately, even while a response is still streaming.
  * `**bold**`, fenced code blocks, lists, headers — all render with
    proper terminal styling, not as literal markdown syntax.
  * Streaming works in-place: the in-flight response is the last
    entry in the history list, re-rendered on every token chunk;
    Application.invalidate() repaints the history pane.

The Worker handles the wire protocol; ChatUI only owns what the human
types and sees.
"""
from __future__ import annotations

import asyncio
import io
import shutil
from typing import TYPE_CHECKING

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text as RichText

from .. import ui

if TYPE_CHECKING:
    from ..worker import Worker


INDENT = "  "


# Subtle dark fill behind the user's input row + echoed prompts.
# Visually separates "what the user said" from "what the model said"
# above it (matches the Claude-Code look).
_PROMPT_BG = "#2d2d2d"


_PROMPT_STYLE = Style.from_dict({
    "prompt":          "ansimagenta bold",
    "rule":            "fg:#5a5a5a bg:default",
    "banner.star":     "fg:ansiyellow bold bg:default",
    "banner.dot":      "fg:#7a4b7a bg:default",
    "banner.name":     "fg:ansimagenta bold bg:default",
    "banner.version":  "fg:#888888 bg:default",
    "banner.key":      "fg:ansicyan bg:default",
    "banner.value":    "fg:default bg:default",
    "status.marker":   "fg:ansimagenta bg:default bold",
    "status.tps":      "fg:ansicyan bg:default",
    "status.dim":      "fg:#888888 bg:default",
    "status.spinner":  "fg:ansicyan bold bg:default",
    "status.notice":   "fg:#aaaaaa bg:default",
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


def _render_markdown_to_ansi(text: str, width: int) -> str:
    """Render markdown via rich and capture the ANSI-encoded output so
    prompt_toolkit's FormattedTextControl can display it via ANSI().

    Layout matches Claude Code: a dimmed `⎿ ` corner marker in a narrow
    left column, then the markdown content overflowing into the right
    column. Code blocks get monokai theming."""
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        width=max(20, width),
        legacy_windows=False,
        soft_wrap=False,
    )
    md = Markdown(text or "…", code_theme="monokai")
    t = Table.grid(padding=(0, 0), pad_edge=False)
    t.add_column(no_wrap=True, width=5)
    t.add_column(overflow="fold")
    t.add_row(RichText(f"{INDENT}⎿ ", style="dim"), md)
    console.print(t)
    return buf.getvalue()


def _render_user_prompt_to_ansi(text: str, width: int) -> str:
    """Echo a sent prompt as `  › <text>` with a dark full-width fill,
    so user messages in history visually separate from the rendered
    markdown responses above them.

    Width-pad with spaces in the same background so the fill extends
    to the right edge of the terminal — single-color text isn't enough
    on its own because terminals don't auto-extend a bg past the
    last visible character.
    """
    buf = io.StringIO()
    console = Console(
        file=buf, force_terminal=True, color_system="truecolor",
        width=max(20, width), legacy_windows=False, soft_wrap=False,
    )
    line = RichText()
    bg = f"on {_PROMPT_BG}"
    line.append(f"{INDENT}› ", style=f"bold magenta {bg}")
    line.append(text, style=bg)
    pad = max(0, width - len(INDENT) - 2 - len(text))
    if pad > 0:
        line.append(" " * pad, style=bg)
    console.print(line)
    return buf.getvalue()


def _render_plain_notice_to_ansi(text: str, width: int) -> str:
    """Dim italic single-line note — used for /reset confirmations and
    error messages that aren't model output."""
    buf = io.StringIO()
    console = Console(
        file=buf, force_terminal=True, color_system="truecolor",
        width=max(20, width), legacy_windows=False, soft_wrap=False,
    )
    console.print(RichText(f"{INDENT}{text}", style="dim italic"))
    return buf.getvalue()


class ChatUI:
    """Interactive chat surface. Persistent HSplit Application from
    prompt_toolkit; rich-rendered ANSI for the history content."""

    def __init__(self, worker: "Worker"):
        self.worker = worker
        self._app: Application | None = None
        # Ordered list of rendered ANSI strings, one per "history item"
        # (user prompt, assistant response, notice, etc.). The history
        # pane joins these into the text it displays.
        self._history_items: list[str] = []
        # Index of the in-flight assistant response (so streaming can
        # replace it on every token). None when nothing is generating.
        self._streaming_idx: int | None = None
        self._latest_tps: float | None = None
        self._latest_decode_tokens: int = 0
        self._latest_decode_s: float = 0.0
        # Pool state shown in the status row when the hub reports
        # `not_ready`. None = serving normally; otherwise one of the
        # values in _POOL_STATE_TEXT.
        self._pool_state: str | None = None
        self._spinner_frame = 0

    # ─────────────────────────── pre-flight ──────────────────────────

    async def _wait_for_pool_ready(self, worker: "Worker") -> None:
        """Animate a spinner on stdout until the hub reports the pool
        can serve. `\\r`-rewrites a single line; clears it when ready.
        Ctrl-C cancels (raises asyncio.CancelledError)."""
        import sys
        try:
            status = await worker.get_pool_status()
        except Exception:
            status = {"ready": True}
        if status.get("ready"):
            return
        try:
            frame = 0
            last_msg = ""
            while True:
                state = status.get("state", "loading")
                msg = _POOL_STATE_TEXT.get(state, "Loading")
                spinner = _frame_at(frame)
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
            sys.stdout.write("\r" + " " * max(len(last_msg), 60) + "\r")
            sys.stdout.flush()

    # ─────────────────────────── main loop ───────────────────────────

    async def run(self) -> None:
        worker = self.worker
        await self._wait_for_pool_ready(worker)
        hub_addr = f"{worker.hub_host}:{worker.hub_port}"
        banner_segments = ui.banner_segments(
            worker.model_id, hub_addr, layer_range=None,
        )

        input_buf = Buffer(multiline=False)
        prompt_queue: asyncio.Queue[str] = asyncio.Queue()
        generating = {"flag": False}

        intro = (f"{INDENT}/help · /reset · /usage · Ctrl-D to exit\n")
        self._history_items.append(
            _render_plain_notice_to_ansi(intro, _ttysize()[0])
        )

        def history_text():
            """FormattedTextControl callable. Joins the per-item ANSI
            strings and wraps via prompt_toolkit's ANSI() adapter so
            escape codes get converted to formatted_text segments."""
            return ANSI("".join(self._history_items))

        def top_rule():
            width = max(20, _ttysize()[0] - 1)
            return [("class:rule", INDENT + "─" * (width - len(INDENT)))]

        bottom_rule = top_rule

        def status_text():
            parts: list[tuple[str, str]] = [
                ("class:status.dim", INDENT),
            ]
            if self._pool_state is not None:
                frame = _SPINNER_FRAMES[self._spinner_frame % len(_SPINNER_FRAMES)]
                msg = _POOL_STATE_TEXT.get(self._pool_state, "Loading")
                parts.append(("class:status.spinner", f"{frame} "))
                parts.append(("class:status.notice", f"{msg}  ·  "))
                parts.append(("class:status.dim", "ctrl-c to cancel"))
                return parts
            if self._latest_tps is not None:
                parts.append((
                    "class:status.dim",
                    f"{self._latest_tps:.1f} tok/s · "
                    f"{self._latest_decode_tokens} tokens in "
                    f"{self._latest_decode_s:.1f}s  ·  ",
                ))
            if generating["flag"]:
                parts.append(("class:status.dim", "generating · "))
            parts.append(("class:status.dim", "ctrl-c to interrupt"))
            return parts

        # ─────────── key bindings ───────────
        kb = KeyBindings()

        @kb.add("enter")
        def _on_enter(event):
            text = input_buf.text
            input_buf.reset()
            stripped = text.strip()
            if not stripped:
                return
            prompt_queue.put_nowait(stripped)

        @kb.add("c-d")
        def _on_ctrl_d(event):
            event.app.exit()

        @kb.add("c-c")
        def _on_ctrl_c(event):
            event.app.exit()

        # ─────────── layout ───────────
        layout = Layout(
            HSplit([
                Window(FormattedTextControl(banner_segments),
                       height=4, dont_extend_height=True),
                Window(FormattedTextControl(history_text, focusable=False),
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

        # ─────────── background tasks ───────────
        processor_task = asyncio.create_task(self._chat_processor(prompt_queue, generating))
        spinner_task = asyncio.create_task(self._spinner_tick())
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

    # ─────────────────────────── helpers ─────────────────────────────

    def _append_history(self, ansi_text: str) -> int:
        """Append a rendered ANSI block to history and invalidate so
        the pane redraws. Returns the index of the new item so callers
        can replace it later (used for streaming responses)."""
        self._history_items.append(ansi_text)
        if self._app is not None:
            self._app.invalidate()
        return len(self._history_items) - 1

    def _replace_history(self, idx: int, ansi_text: str) -> None:
        """Overwrite an existing history item — used to update the
        in-flight assistant response on every streamed token."""
        if 0 <= idx < len(self._history_items):
            self._history_items[idx] = ansi_text
            if self._app is not None:
                self._app.invalidate()

    async def _spinner_tick(self) -> None:
        """Advance the spinner frame while a pool-not-ready state is
        active so the status row animates. Idle otherwise."""
        while True:
            await asyncio.sleep(0.18)
            if self._pool_state is None:
                continue
            self._spinner_frame = (self._spinner_frame + 1) % len(_SPINNER_FRAMES)
            if self._app is not None:
                self._app.invalidate()

    async def _chat_processor(
        self, prompt_queue: asyncio.Queue, generating: dict,
    ) -> None:
        worker = self.worker
        while True:
            try:
                user_msg = await prompt_queue.get()
            except asyncio.CancelledError:
                return

            # Echo the user's line to history first (always — even if
            # this turns out to be a slash command, the user wants
            # the record of what they typed).
            self._append_history(_render_user_prompt_to_ansi(
                user_msg, _ttysize()[0],
            ))

            if user_msg.startswith("/"):
                if await self._handle_slash_command(user_msg):
                    continue
                # unknown slash command — fall through, treat as prompt

            generating["flag"] = True
            if self._app is not None:
                self._app.invalidate()
            try:
                await self._stream_one_turn(user_msg)
            finally:
                generating["flag"] = False
                # Blank-line spacer between turns so the next `›` prompt
                # in history doesn't visually butt against the previous
                # response.
                self._append_history("\n")
                if self._app is not None:
                    self._app.invalidate()

    async def _stream_one_turn(self, user_msg: str) -> None:
        """One prompt → response cycle. Reserves a history slot for
        the response and re-renders it on every streamed token."""
        worker = self.worker
        # dots.worker is one package level up — `.worker` would resolve
        # to dots.chat.worker (doesn't exist).
        from ..worker import PoolNotReadyError

        # Reserve a slot; we'll replace it on each token.
        self._streaming_idx = self._append_history(
            _render_markdown_to_ansi("", _ttysize()[0]),
        )
        accumulated = {"value": ""}

        def on_token(t: str) -> None:
            accumulated["value"] += t
            rendered = _render_markdown_to_ansi(
                accumulated["value"], _ttysize()[0],
            )
            assert self._streaming_idx is not None
            self._replace_history(self._streaming_idx, rendered)

        # Retry loop for transient PoolNotReady (worker dropped, etc.).
        while True:
            try:
                _, resp = await worker.send_prompt(
                    user_msg, max_tokens=256,
                    on_token=on_token,
                )
                self._pool_state = None
                decode_tokens = int(resp.get("decode_tokens", 0))
                decode_s = float(resp.get("decode_ms", 0)) / 1000.0
                if decode_tokens > 0 and decode_s > 0:
                    self._latest_tps = decode_tokens / decode_s
                    self._latest_decode_tokens = decode_tokens
                    self._latest_decode_s = decode_s
                break
            except PoolNotReadyError as nr:
                self._pool_state = nr.state
                if self._app is not None:
                    self._app.invalidate()
                await asyncio.sleep(2.0)
            except RuntimeError as e:
                # Replace the in-flight response with an error notice
                # instead of leaving an empty response in history.
                self._replace_history(
                    self._streaming_idx,
                    _render_plain_notice_to_ansi(str(e), _ttysize()[0]),
                )
                self._streaming_idx = None
                return
        self._streaming_idx = None

    # ──────────────────────── slash commands ─────────────────────────

    async def _handle_slash_command(self, cmd: str) -> bool:
        """Return True if the command was handled. False means
        "not one of ours; treat as a prompt"."""
        worker = self.worker
        head, _, _ = cmd.partition(" ")
        if head == "/reset":
            try:
                await worker.conn.call("reset")
            except Exception:
                pass
            self._append_history(_render_plain_notice_to_ansi(
                "[conversation reset]", _ttysize()[0],
            ))
            return True
        if head == "/help":
            self._render_help()
            return True
        if head == "/usage":
            await self._render_usage()
            return True
        return False

    def _render_help(self) -> None:
        rows = [
            ("/help",  "show this help"),
            ("/reset", "clear the conversation history"),
            ("/usage", "show CU earned/spent across the pool"),
            ("Ctrl-D", "exit the chat"),
        ]
        buf = io.StringIO()
        console = Console(
            file=buf, force_terminal=True, color_system="truecolor",
            width=max(20, _ttysize()[0]), legacy_windows=False,
        )
        t = Table.grid(padding=(0, 2))
        t.add_column(style="cyan", no_wrap=True)
        t.add_column(style="dim")
        for c, d in rows:
            t.add_row(f"{INDENT}{c}", d)
        console.print(t)
        self._append_history(buf.getvalue())

    async def _render_usage(self) -> None:
        worker = self.worker
        hub_host = worker.hub_host
        url = f"http://{hub_host}:8080/v1/credits"
        try:
            import json
            import urllib.request
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3.0) as r:
                data = json.loads(r.read())
        except Exception as e:
            self._append_history(_render_plain_notice_to_ansi(
                f"/usage failed: {e}", _ttysize()[0],
            ))
            return

        accounts = data.get("accounts", {}) or {}
        my_acc = getattr(worker, "account_id", None)
        buf = io.StringIO()
        console = Console(
            file=buf, force_terminal=True, color_system="truecolor",
            width=max(20, _ttysize()[0]), legacy_windows=False,
        )
        t = Table(
            show_header=True, header_style="bold", box=None,
            padding=(0, 2, 0, 0),
        )
        t.add_column(f"{INDENT}account")
        t.add_column("balance", justify="right")
        t.add_column("earned", justify="right")
        t.add_column("spent",  justify="right")
        t.add_column("turns",  justify="right")
        ordered = sorted(
            accounts.values(),
            key=lambda a: (a.get("account_id") != my_acc,
                            -float(a.get("balance_cu", 0))),
        )
        for a in ordered:
            alias = a.get("alias") or a.get("account_id", "")[:10]
            is_me = a.get("account_id") == my_acc
            label = (f"[bold cyan]{INDENT}{alias}[/bold cyan]"
                     if is_me else f"{INDENT}{alias}")
            t.add_row(
                label,
                f"{a.get('balance_cu', 0):+.4f} CU",
                f"{a.get('earned_cu', 0):.4f}",
                f"{a.get('spent_cu', 0):.4f}",
                str(a.get("turns_as_querier", 0)
                    + a.get("turns_as_worker", 0)),
            )
        console.print(t)
        self._append_history(buf.getvalue())

"""Worker: joins a hub as both compute participant and chat client.

One process; two roles that share a single TCP connection to the hub:

  • Worker role (driven by the hub):
      LOAD_SHARD          → build a Shard for the assigned layer range
      COMPUTE             → run our layer slice on a hidden state
      RESET_CONVERSATION  → clear KV cache for a conv id

  • Chat role (driven by the user typing in the terminal):
      PROMPT          we send our text + conv_id; hub runs a turn
      TOKEN           hub streams response tokens back as notifications
      WELCOME         hub tells us our conv_id (assigned on HELLO)
      CONVERSATION_RESET   hub tells us our conv was reset (topology change)
      TURN_DONE       reply to our PROMPT call signaling end of turn

A single asyncio task tree: the chat REPL coroutine awaits the user's
input and sends PROMPT calls; the PeerConnection's read loop dispatches
incoming compute/load/etc to handler in parallel; token notifications hit
the same handler and write to stdout.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.styles import Style

from . import ui
from .peer import PeerConnection
from .protocol import decode_array, encode_array
from .shard import Shard, ShardSpec
from .util import free_ram_gb


# 2-space leading margin for *everything* in the chat area. The `›` lands
# at col 2 so user lines start at col 4 (after "  › "); response lines also
# start at col 4 so they sit under the user's text — same as Claude Code.
INDENT = "  "


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
                    continue          # don't start a line with whitespace
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
        # Wrap to next line if the word doesn't fit on this one (but only
        # if we'd actually save space — a word longer than the wrap width
        # just gets emitted as-is).
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

log = logging.getLogger("dots.worker")


def _ttysize() -> tuple[int, int]:
    try:
        import shutil
        return shutil.get_terminal_size((80, 24))
    except Exception:
        return (80, 24)

_PROMPT_STYLE = Style.from_dict({
    "prompt": "ansimagenta bold",
    "": "",
    # bottom-toolbar (used in one-shot fallback) without reverse video.
    "bottom-toolbar": "bg:default fg:default noreverse",
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
})


class Worker:
    def __init__(self, hub_host: str, hub_port: int,
                 one_shot: str | None = None, max_tokens: int = 128):
        self.hub_host = hub_host
        self.hub_port = hub_port
        self.conn: PeerConnection | None = None
        self.shard: Shard | None = None
        self.conv_id: str | None = None        # assigned by hub via WELCOME
        self.model_id: str | None = None
        self.n_layers: int | None = None
        self.one_shot = one_shot
        self.max_tokens = max_tokens
        # Serializes LOAD_SHARD vs COMPUTE so we can't compute through a
        # half-replaced shard during a re-plan.
        self._shard_lock = asyncio.Lock()
        # Set when the hub sends our WELCOME; the REPL waits on this so we
        # don't start chatting before we know our conv_id.
        self._welcome = asyncio.Event()
        # Capture-mode plumbing for programmatic use (e.g. tests). When
        # _capture_text is not None, every "token" notification also appends
        # to it instead of only going to stdout.
        self._capture_text: list[str] | None = None
        # Rolling status — shown on the divider above each new prompt.
        self._latest_tps: float | None = None
        # Set while the interactive prompt_toolkit Application is running.
        self._app: Application | None = None
        # When set, every TOKEN notification is delivered to this callback
        # instead of being written to stdout. Used by the interactive UI to
        # route streaming output into its history buffer.
        self._token_callback = None

    # ───────────────────────────── lifecycle ─────────────────────────────

    async def start(self) -> None:
        ui.info(f"connecting to hub at {self.hub_host}:{self.hub_port}…")
        reader, writer = await asyncio.open_connection(self.hub_host, self.hub_port)
        self.conn = PeerConnection(
            reader, writer, f"{self.hub_host}:{self.hub_port}", self._handle
        )
        ack = await self.conn.call("hello", free_ram_gb=free_ram_gb())
        if ack.get("kind") != "hello_ack":
            raise RuntimeError(f"unexpected HELLO response: {ack}")
        self.model_id = ack.get("model_id")
        self.n_layers = ack.get("n_layers")
        ui.info(f"hub accepted; model={self.model_id} ({self.n_layers} layers)")

        try:
            await self._welcome.wait()
        except asyncio.CancelledError:
            return

        # Run the chat REPL until the user exits or the connection drops.
        repl_task = asyncio.create_task(self._chat_loop())
        closed_task = asyncio.create_task(self.conn._closed.wait())
        done, pending = await asyncio.wait(
            {repl_task, closed_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        if closed_task in done:
            ui.notice("hub disconnected — exiting")

    # ──────────────────────────── incoming messages ──────────────────────

    async def _handle(self, msg: dict) -> dict | None:
        kind = msg.get("kind")
        if kind == "load_shard":
            return await self._handle_load_shard(msg)
        if kind == "compute":
            return await self._handle_compute(msg)
        if kind == "reset_conversation":
            if self.shard:
                async with self._shard_lock:
                    self.shard.reset_conversation(msg["conv_id"])
            return {"kind": "ok"}
        if kind == "token":
            # Notification — route to whichever consumer is registered.
            # Interactive UI installs a callback that appends to the
            # history buffer; one-shot/CLI paths fall back to stdout.
            text = msg.get("text", "")
            if self._token_callback is not None:
                try:
                    self._token_callback(text)
                except Exception:
                    log.exception("token callback failed")
            else:
                sys.stdout.write(text)
                sys.stdout.flush()
            if self._capture_text is not None:
                self._capture_text.append(text)
            return None
        if kind == "welcome":
            self.conv_id = msg.get("conv_id")
            self._welcome.set()
            return None
        if kind == "ping":
            return {"kind": "pong"}
        return {"kind": "error", "error": f"unknown kind: {kind}"}

    async def _handle_load_shard(self, msg: dict) -> dict:
        spec = ShardSpec(
            model_id=msg["model_id"],
            layer_start=int(msg["layer_start"]),
            layer_end=int(msg["layer_end"]),
        )
        ui.info(f"loading shard [{spec.layer_start}:{spec.layer_end}]…")
        async with self._shard_lock:
            self.shard = None  # free old weights before allocating new
            self.shard = await asyncio.to_thread(Shard, spec)
        ui.info(f"shard ready [{spec.layer_start}:{spec.layer_end}]")
        return {"kind": "ready", "n_layers": self.shard.n_layers}

    async def _handle_compute(self, msg: dict) -> dict:
        async with self._shard_lock:
            if self.shard is None:
                return {"kind": "error", "error": "no shard loaded"}
            hidden = decode_array(msg["hidden"])
            out = self.shard.run_layers(msg["conv_id"], hidden)
            return {"kind": "compute_out", "hidden": encode_array(out)}

    # ─────────────────────────────── chat REPL ──────────────────────────

    async def send_prompt(
        self, text: str, max_tokens: int = 128, temperature: float | None = None,
    ) -> tuple[str, dict]:
        """Send one prompt and return (response_text, stats_dict).

        Captures the streamed token notifications into a buffer alongside
        printing them to stdout, so callers (e.g. tests) get clean access
        to the response. Blocks until the hub returns turn_done or an error.
        """
        if self.conn is None:
            raise RuntimeError("not connected to hub")
        payload: dict = {"text": text, "max_tokens": max_tokens}
        if temperature is not None:
            payload["temperature"] = temperature
        captured: list[str] = []
        prev_capture = self._capture_text
        self._capture_text = captured
        try:
            resp = await self.conn.call("prompt", **payload)
        finally:
            self._capture_text = prev_capture
        if resp.get("kind") != "turn_done":
            raise RuntimeError(resp.get("error", "turn failed"))
        return "".join(captured), resp

    async def _chat_loop(self) -> None:
        hub_addr = f"{self.hub_host}:{self.hub_port}"
        layer_range = (
            (self.shard.spec.layer_start, self.shard.spec.layer_end)
            if self.shard is not None else None
        )

        # One-shot mode: still uses the simpler PromptSession-free path,
        # since there's nothing to display interactively.
        if self.one_shot is not None:
            ui.console.print()
            ui.banner(self.model_id, hub_addr, layer_range)
            ui.console.print(f"\n{INDENT}› {self.one_shot}\n")
            try:
                _, resp = await self.send_prompt(
                    self.one_shot, max_tokens=self.max_tokens,
                )
                sys.stdout.write("\n")
                ui.stats(
                    int(resp.get("prefill_tokens", 0)),
                    float(resp.get("prefill_ms", 0)),
                    int(resp.get("decode_tokens", 0)),
                    float(resp.get("decode_ms", 0)) / 1000.0,
                )
            except RuntimeError as e:
                ui.error(str(e))
            return

        # Interactive: a small prompt_toolkit Application with a history
        # buffer (read-only-ish, full of past prompts + streamed responses)
        # above a fixed input box at the bottom of the screen. The user can
        # type the next message at any moment, even while a response is
        # still streaming in — submitted messages queue and process FIFO.
        await self._run_chat_app(hub_addr, layer_range)

    # ─────────────────────────── interactive app ────────────────────────

    async def _run_chat_app(
        self, hub_addr: str, layer_range: tuple[int, int] | None,
    ) -> None:
        history_buf = Buffer()
        input_buf = Buffer(multiline=False)
        # Response indent is 4 (under "  › ") and we wrap at the terminal width.
        wrapper = _Wrapper(indent=INDENT + "  ", width=_ttysize()[0] - 1)
        prompt_queue: asyncio.Queue[str] = asyncio.Queue()
        generating = {"flag": False}     # mutable so closures can flip it

        # Help line lives in the history buffer (so it scrolls with the
        # conversation); the colored banner gets its own static Window above.
        intro = f"\n{INDENT}/reset to clear conversation · Ctrl-D to exit\n\n"
        history_buf.set_document(Document(intro, cursor_position=len(intro)),
                                 bypass_readonly=True)
        banner_segments = ui.banner_segments(
            self.model_id, hub_addr, layer_range,
        )

        def append_history(text: str) -> None:
            # Auto-follow when the cursor is already at the end. If the user
            # scrolled up to read past content, leave the cursor where they
            # parked it so the view doesn't snap back to the bottom on every
            # streamed token.
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
            # Echo the submitted prompt into history immediately so the user
            # sees their message land.
            append_history(f"{INDENT}› {stripped}\n")

        @kb.add("c-d")
        def _on_ctrl_d(event):
            event.app.exit()

        @kb.add("c-c")
        def _on_ctrl_c(event):
            # Same as Ctrl-D: exit the chat and disconnect from the hub.
            # (Mid-turn cancellation of the in-flight generation is a v2
            # feature; v1 just exits cleanly.)
            event.app.exit()

        # ── Scrolling: move the history buffer's cursor; the Window scrolls
        #    to keep that cursor visible. Auto-follow (cursor at end) resumes
        #    naturally once the user scrolls all the way back to the bottom.
        def _scroll(rows: int) -> None:
            doc = history_buf.document
            target_row = max(0, doc.cursor_position_row + rows)
            target_col = 0
            new_pos = doc.translate_row_col_to_index(target_row, target_col)
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
                # Colored banner — fixed, never scrolled.
                Window(FormattedTextControl(banner_segments),
                       height=4, dont_extend_height=True),
                # Conversation history.
                Window(BufferControl(history_buf, focusable=False),
                       wrap_lines=False, dont_extend_height=True),
                # Input box.
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
            # Render inline rather than taking over the terminal: nothing
            # is cleared on startup, and the conversation stays in the
            # scrollback after Ctrl-C / Ctrl-D.
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
                        await self.conn.call("reset")
                    except Exception:
                        pass
                    append_history(f"{INDENT}[conversation reset]\n\n")
                    continue
                wrapper.set_width(_ttysize()[0] - 1)
                wrapper.reset()
                generating["flag"] = True
                self._app.invalidate()
                try:
                    _, resp = await self._send_prompt_streaming(
                        user_msg,
                        on_token=lambda t: append_history(wrapper.feed(t)),
                    )
                    # Flush any final partial word that didn't end on a space.
                    tail = wrapper.flush()
                    if tail:
                        append_history(tail)
                    decode_tokens = int(resp.get("decode_tokens", 0))
                    decode_s = float(resp.get("decode_ms", 0)) / 1000.0
                    if decode_tokens > 0 and decode_s > 0:
                        self._latest_tps = decode_tokens / decode_s
                except RuntimeError as e:
                    append_history(f"{INDENT}error: {e}\n")
                finally:
                    generating["flag"] = False
                    # Normalize end-of-turn whitespace: strip any trailing
                    # newlines the model emitted, then add exactly one blank
                    # line before the input box.
                    text = history_buf.text.rstrip("\n") + "\n\n"
                    history_buf.set_document(
                        Document(text, cursor_position=len(text)),
                        bypass_readonly=True,
                    )
                    self._app.invalidate()

        processor_task = asyncio.create_task(chat_processor())
        try:
            await self._app.run_async()
        finally:
            processor_task.cancel()
            try:
                await processor_task
            except (asyncio.CancelledError, Exception):
                pass
            self._app = None

    async def _send_prompt_streaming(
        self, text: str, on_token, max_tokens: int = 256,
        temperature: float | None = None,
    ) -> tuple[str, dict]:
        """Like send_prompt but routes each TOKEN notification through
        `on_token(text)` (used by the chat app to append to its history
        buffer instead of writing to stdout)."""
        if self.conn is None:
            raise RuntimeError("not connected to hub")
        payload: dict = {"text": text, "max_tokens": max_tokens}
        if temperature is not None:
            payload["temperature"] = temperature
        captured: list[str] = []

        prev_callback = self._token_callback
        self._token_callback = lambda t: (on_token(t), captured.append(t))
        try:
            resp = await self.conn.call("prompt", **payload)
        finally:
            self._token_callback = prev_callback
        if resp.get("kind") != "turn_done":
            raise RuntimeError(resp.get("error", "turn failed"))
        return "".join(captured), resp

"""Interactive chat UI — terminal-native scrollback, rich-rendered output.

Everything visible above the input bar is printed via `rich.Console`
straight to the terminal, so the terminal's own scrollback captures
the entire conversation. Mouse-wheel scroll just works; resizing the
terminal redraws naturally; older content never gets clipped by a
managed window.

Only the single-line input bar is "live" — `prompt_toolkit`'s
`PromptSession` owns the bottom row. `patch_stdout()` routes any
output that arrives while a prompt is open to appear above the bar,
so streaming responses don't fight the input cursor.

The Worker handles the wire protocol + token notifications; ChatUI
just owns what the human types and sees.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.style import Style as RichStyle
from rich.table import Table
from rich.text import Text

from ... import ui
from .spinner import (
    POOL_STATE_TEXT as _POOL_STATE_TEXT,
    fmt_bytes as _fmt_bytes,
    frame_at as _frame_at,
    render_bar as _render_bar,
)

if TYPE_CHECKING:
    from ...distributed.worker import Worker


INDENT = "  "

# Subtle dark fill behind sent prompts so user messages visually
# separate from the model's rendered markdown responses.
_PROMPT_BG = "#2d2d2d"


_PROMPT_STYLE = Style.from_dict(
    {
        "prompt": f"ansimagenta bold bg:{_PROMPT_BG}",
        "": f"bg:{_PROMPT_BG}",
    }
)


_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


def _split_think(text: str) -> tuple[str, str]:
    """Split a response into (thinking, answer).

    Reasoning models (e.g. Qwen3) emit a chain-of-thought terminated by
    `</think>`; the opening `<think>` is usually injected by the chat
    template into the prompt, so it's often absent from the output. We
    only treat text as thinking once we've seen a close tag (or an
    explicit open tag) — otherwise it's the answer, so non-thinking
    models render normally."""
    close = text.find(_THINK_CLOSE)
    if close != -1:
        think, answer = text[:close], text[close + len(_THINK_CLOSE) :]
    elif _THINK_OPEN in text:
        think, answer = text, ""
    else:
        return "", text
    return think.replace(_THINK_OPEN, "").strip(), answer.strip()


def _as_markdown(text: str) -> str:
    """Markdown source for one response: the thinking trace as a
    blockquote (`>` notes) above the answer."""
    think, answer = _split_think(text)
    blocks: list[str] = []
    if think:
        blocks.append(
            "\n".join(f"> {ln}" if ln else ">" for ln in think.splitlines())
        )
    if answer:
        blocks.append(answer)
    return "\n\n".join(blocks) if blocks else "…"


class ChatUI:
    """Interactive chat surface for a Worker that's already connected
    to a hub. Output is stdout-native; only the prompt bar is live."""

    def __init__(self, worker: "Worker"):
        self.worker = worker
        self._console = Console()
        # Cumulative across the chat session. Average TPS is the ratio;
        # `rprompt` (right-hand side of the live input bar) renders
        # them as a single right-aligned dim string.
        self._total_decode_tokens: int = 0
        self._total_decode_s: float = 0.0

    # ─────────────────────────── pre-flight ──────────────────────────

    async def _wait_for_pool_ready(self, worker: "Worker") -> None:
        """Animate a spinner on stdout until the hub reports the pool
        can serve. `\\r`-rewrites a single line; clears it when ready.
        Ctrl-C cancels (raises asyncio.CancelledError)."""
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
                        if bytes_total > 0
                        else ""
                    )
                    line = (
                        f"  {spinner}  {msg}  "
                        f"{_render_bar(float(progress))}{size_suffix}"
                    )
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

        hub_addr = f"hub:{worker.hub_ticket[:12]}…"
        ui.banner(
            worker.requested_model_id or worker.model_id, hub_addr, layer_range=None
        )
        self._console.print(
            Text(INDENT + "/help · /reset · /usage · Ctrl-D to exit\n", style="dim"),
        )

        def rprompt():
            """Right-aligned content on the prompt line: cumulative
            tok/s, total tokens, total decode seconds."""
            if self._total_decode_s <= 0:
                return ""
            avg = self._total_decode_tokens / self._total_decode_s
            return HTML(
                f'<style fg="#888888">'
                f"{avg:.1f} tok/s · {self._total_decode_tokens} tokens "
                f"in {self._total_decode_s:.1f}s"
                f"</style>"
            )

        # `erase_when_done` clears prompt_toolkit's own rendered input
        # line on accept; otherwise it stays in scrollback and we'd see
        # the typed text twice — once from PT, once from the explicit
        # `_echo_user_prompt` below.
        session: PromptSession = PromptSession(
            style=_PROMPT_STYLE, erase_when_done=True
        )

        while True:
            try:
                with patch_stdout(raw=True):
                    user_msg = await session.prompt_async(
                        HTML("<prompt>  › </prompt>"),
                        rprompt=rprompt,
                    )
            except (EOFError, KeyboardInterrupt):
                break

            stripped = (user_msg or "").strip()
            if not stripped:
                continue

            # Echo the user's line into scrollback first (always — even
            # if it turns out to be a slash command, the user wants
            # the record of what they typed).
            self._echo_user_prompt(stripped)

            if stripped.startswith("/"):
                if await self._handle_slash_command(stripped):
                    continue

            try:
                await self._stream_turn(stripped)
            except ConnectionError as e:
                # Hub went away mid-turn (or between turns). Nothing left
                # to talk to — print a clean notice and leave the loop
                # rather than letting the error escape as an unhandled
                # task exception ("Task exception was never retrieved").
                self._console.print(
                    Text(f"{INDENT}connection to hub lost ({e}) — exiting.", style="red"),
                )
                break

    # ─────────────────────────── echo helpers ────────────────────────

    def _echo_user_prompt(self, text: str) -> None:
        """Print `  › <text>` to scrollback with a dark full-width
        fill so user messages visually separate from rendered
        markdown responses."""
        bg = RichStyle(bgcolor=_PROMPT_BG)
        marker_style = RichStyle(color="magenta", bold=True, bgcolor=_PROMPT_BG)
        line = Text()
        line.append(f"{INDENT}› ", style=marker_style)
        line.append(text, style=bg)
        # Pad to terminal width so the bg extends to the right edge.
        pad = max(0, self._console.width - len(INDENT) - 2 - len(text))
        if pad > 0:
            line.append(" " * pad, style=bg)
        self._console.print(line)

    # ─────────────────────────── one turn ────────────────────────────

    async def _stream_turn(self, prompt_text: str) -> None:
        """One prompt → response cycle. rich.Live owns the rendering
        area during streaming; on exit it commits the final state to
        terminal scrollback as plain output."""
        worker = self.worker
        from ...distributed.worker import PoolNotReadyError

        def render(text: str):
            """Per-frame renderable. Table.grid puts a dim `⎿ ` marker
            in a narrow left column, then markdown in the wide right
            column — matches Claude Code's response gutter. A model's
            reasoning trace renders as a blockquote above the answer."""
            md = Markdown(_as_markdown(text), code_theme="monokai")
            t = Table.grid(padding=(0, 0), pad_edge=False)
            t.add_column(no_wrap=True, width=5)
            t.add_column(overflow="fold")
            t.add_row(Text(f"{INDENT}⎿ ", style="dim"), md)
            return t

        accumulated = {"value": ""}
        while True:
            try:
                with Live(
                    render(""),
                    console=self._console,
                    refresh_per_second=15,
                    transient=False,
                ) as live:

                    def on_token(t: str) -> None:
                        accumulated["value"] += t
                        live.update(render(accumulated["value"]))

                    # Guardrail: bound a single interactive response. A
                    # degenerate model (small/quantized) can loop forever;
                    # the repetition penalty in sampling is the real fix,
                    # this just caps the blast radius if it slips through.
                    _, resp = await worker.send_prompt(
                        prompt_text,
                        max_tokens=512,
                        on_token=on_token,
                    )
                decode_tokens = int(resp.get("decode_tokens", 0))
                decode_s = float(resp.get("decode_ms", 0)) / 1000.0
                if decode_tokens > 0 and decode_s > 0:
                    self._total_decode_tokens += decode_tokens
                    self._total_decode_s += decode_s
                break
            except PoolNotReadyError as nr:
                self._console.print(
                    Text(
                        f"{INDENT}{_POOL_STATE_TEXT.get(nr.state, 'Loading')}…",
                        style="dim italic",
                    ),
                )
                await asyncio.sleep(2.0)
                continue
            except RuntimeError as e:
                self._console.print(Text(f"{INDENT}{e}", style="red"))
                return

        # Trailing blank line so the next user prompt has breathing room.
        self._console.print()

    # ──────────────────────── slash commands ─────────────────────────

    async def _handle_slash_command(self, cmd: str) -> bool:
        worker = self.worker
        head, _, _ = cmd.partition(" ")
        if head == "/reset":
            try:
                await worker.conn.call("reset")
            except Exception:
                pass
            self._console.print(
                Text(INDENT + "[conversation reset]", style="dim italic"),
            )
            self._console.print()
            return True
        if head == "/help":
            self._print_help()
            return True
        if head == "/usage":
            await self._print_usage()
            return True
        return False

    def _print_help(self) -> None:
        rows = [
            ("/help", "show this help"),
            ("/reset", "clear the conversation history"),
            ("/usage", "show CU earned/spent across the pool"),
            ("Ctrl-D", "exit the chat"),
        ]
        t = Table.grid(padding=(0, 2))
        t.add_column(style="cyan", no_wrap=True)
        t.add_column(style="dim")
        for c, d in rows:
            t.add_row(f"{INDENT}{c}", d)
        self._console.print(t)
        self._console.print()

    async def _print_usage(self) -> None:
        worker = self.worker
        # Ask the hub for the ledger over the iroh control channel — workers
        # behind NAT can't reach the hub's HTTP API by host:port.
        if worker.conn is None:
            self._console.print(
                Text(f"{INDENT}/usage unavailable: not connected", style="red"),
            )
            return
        try:
            resp = await worker.conn.call("credits")
            if resp.get("kind") != "credits":
                raise RuntimeError(resp.get("error", "credits query failed"))
            data = resp.get("data", {})
        except Exception as e:
            self._console.print(
                Text(f"{INDENT}/usage failed: {e}", style="red"),
            )
            return

        accounts = data.get("accounts", {}) or {}
        my_acc = getattr(worker, "account_id", None)
        t = Table(
            show_header=True,
            header_style="bold",
            box=None,
            padding=(0, 2, 0, 0),
        )
        t.add_column(f"{INDENT}account")
        t.add_column("balance", justify="right")
        t.add_column("earned", justify="right")
        t.add_column("spent", justify="right")
        t.add_column("turns", justify="right")
        ordered = sorted(
            accounts.values(),
            key=lambda a: (
                a.get("account_id") != my_acc,
                -float(a.get("balance_cu", 0)),
            ),
        )
        for a in ordered:
            alias = a.get("alias") or a.get("account_id", "")[:10]
            is_me = a.get("account_id") == my_acc
            label = (
                f"[bold cyan]{INDENT}{alias}[/bold cyan]"
                if is_me
                else f"{INDENT}{alias}"
            )
            t.add_row(
                label,
                f"{a.get('balance_cu', 0):+.4f} CU",
                f"{a.get('earned_cu', 0):.4f}",
                f"{a.get('spent_cu', 0):.4f}",
                str(a.get("turns_as_querier", 0) + a.get("turns_as_worker", 0)),
            )
        self._console.print(t)
        self._console.print()

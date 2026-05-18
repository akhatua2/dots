"""Interactive chat UI for dots — Claude-Code style.

Output uses `rich` (markdown rendering + live streaming), input uses a
single-line `prompt_toolkit.PromptSession`. Every assistant turn is a
distinct block in your terminal's scrollback, so:

  * `**bold**`, `*italic*`, headers, lists, fenced code blocks render
    properly via `rich.markdown.Markdown` instead of literal markdown
    syntax leaking through to the user.
  * Streaming works in-place during generation via `rich.live.Live`;
    once the turn finishes, the rendered block is "frozen" into normal
    terminal output and stays in scrollback. Mouse-wheel + native
    terminal scroll just work.
  * Input gets `prompt_toolkit` for the things it's actually good at
    (cursor editing, ctrl-key handling, async input loop).

Both responsibilities are kept tight: the Worker handles the wire
protocol + token streaming, ChatUI handles the human's view.
"""
from __future__ import annotations

import asyncio
import sys
import time
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from .. import ui

if TYPE_CHECKING:
    from ..worker import Worker


INDENT = "  "


# prompt_toolkit input bar styling. Keep the magenta `›` consistent
# with the rest of the dots look.
_PROMPT_STYLE = Style.from_dict({
    "prompt":  "ansimagenta bold",
    "":        "",
})


from .spinner import (
    POOL_STATE_TEXT as _POOL_STATE_TEXT,
    SPINNER_FRAMES as _SPINNER_FRAMES,
    fmt_bytes as _fmt_bytes,
    frame_at as _frame_at,
    render_bar as _render_bar,
)


class ChatUI:
    """Interactive chat surface for a Worker that's already connected to
    a hub. The terminal owns scrollback; rich owns formatting; we just
    coordinate prompt → stream → display."""

    def __init__(self, worker: "Worker"):
        self.worker = worker
        self._console = Console()
        self._pool_state: str | None = None     # for in-line spinner messages
        self._latest_tps: float | None = None

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
        ui.banner(worker.model_id, hub_addr, layer_range=None)
        self._console.print(
            Text(INDENT + "/help · /reset · /usage · Ctrl-D to exit\n",
                 style="dim"),
            soft_wrap=True,
        )

        session: PromptSession = PromptSession(style=_PROMPT_STYLE)

        while True:
            # ───── input ─────
            try:
                with patch_stdout():
                    user_msg = await session.prompt_async(
                        HTML('<prompt>  › </prompt>'),
                    )
            except (EOFError, KeyboardInterrupt):
                break

            stripped = (user_msg or "").strip()
            if not stripped:
                continue
            if stripped.startswith("/"):
                if await self._handle_slash_command(stripped):
                    continue
                # unknown slash command — fall through, treat as prompt

            # ───── stream the response ─────
            await self._stream_turn(stripped)

    # ──────────────────────── slash commands ─────────────────────────

    async def _handle_slash_command(self, cmd: str) -> bool:
        """Handle a `/xxx` command. Returns True if the command was
        recognized (caller skips treating the line as a prompt). False
        means "not one of ours; fall through to the model"."""
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
        """Show the supported slash commands. Kept short so it fits in
        one screen even on small terminals."""
        rows = [
            ("/help",   "show this help"),
            ("/reset",  "clear the conversation history"),
            ("/usage",  "show CU earned/spent across the pool"),
            ("Ctrl-D", "exit the chat"),
        ]
        t = Table.grid(padding=(0, 2))
        t.add_column(style="cyan", no_wrap=True)
        t.add_column(style="dim")
        for c, d in rows:
            t.add_row(c, d)
        self._console.print(Padding(t, (0, 0, 1, 4)))

    async def _print_usage(self) -> None:
        """Hit the hub's HTTP /v1/credits and render the CU ledger
        snapshot in-line. We assume the hub's HTTP port is the default
        (8080); the wire-protocol port is 5555 by convention but the
        HTTP port is independent."""
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
            self._console.print(
                Text(f"{INDENT}/usage failed: {e}", style="red"),
            )
            return

        accounts = data.get("accounts", {}) or {}
        my_acc = getattr(worker, "account_id", None)
        t = Table(
            title=None,
            show_header=True,
            header_style="bold",
            box=None,
            padding=(0, 2, 0, 0),
        )
        t.add_column("account")
        t.add_column("balance", justify="right")
        t.add_column("earned", justify="right")
        t.add_column("spent",  justify="right")
        t.add_column("turns",  justify="right")
        # Render the user's own account first, then the rest.
        ordered = sorted(
            accounts.values(),
            key=lambda a: (a.get("account_id") != my_acc,
                            -float(a.get("balance_cu", 0))),
        )
        for a in ordered:
            alias = a.get("alias") or a.get("account_id", "")[:10]
            is_me = a.get("account_id") == my_acc
            label = f"[bold cyan]{alias}[/bold cyan]" if is_me else alias
            t.add_row(
                label,
                f"{a.get('balance_cu', 0):+.4f} CU",
                f"{a.get('earned_cu', 0):.4f}",
                f"{a.get('spent_cu', 0):.4f}",
                str(a.get("turns_as_querier", 0)
                    + a.get("turns_as_worker", 0)),
            )
        self._console.print(Padding(t, (0, 0, 1, 4)))

    # ─────────────────────────── one turn ────────────────────────────

    async def _stream_turn(self, prompt_text: str) -> None:
        """Run one prompt → response cycle. Streaming markdown updates
        in place via rich.Live; when the response completes, rich.Live
        finalizes and the rendered block stays in scrollback."""
        worker = self.worker

        # Re-import lazily; PoolNotReadyError lives one package up.
        from ..worker import PoolNotReadyError

        accumulated = ""
        loop = asyncio.get_running_loop()

        def renderable_for(text: str):
            """Build the per-frame renderable.

            Layout matches Claude Code: a dimmed `⎿  ` corner marker in
            a narrow left column, then the rendered markdown in the
            wide right column. Table.grid keeps the marker aligned with
            the first line of the response across multi-line answers.
            """
            md = Markdown(text or "…", code_theme="monokai")
            t = Table.grid(padding=(0, 0), pad_edge=False)
            t.add_column(no_wrap=True, width=5)   # "  ⎿  "
            t.add_column(overflow="fold")
            t.add_row(Text(f"{INDENT}⎿ ", style="dim"), md)
            return t

        # Retry loop for transient PoolNotReady (e.g. a worker dropped
        # and replacement is loading). Bounded by Ctrl-C.
        while True:
            t0 = time.perf_counter()
            try:
                # rich.Live owns the entire rendering area for this turn.
                # Once the `with` block exits, the final state is
                # committed to scrollback as plain output.
                with Live(
                    renderable_for(""),
                    console=self._console,
                    refresh_per_second=15,
                    transient=False,
                ) as live:
                    nonlocal_text = {"value": ""}

                    def on_token(t: str) -> None:
                        nonlocal_text["value"] += t
                        # Live update is cheap (rich diffs the render),
                        # so we update on every chunk for smoothness.
                        live.update(renderable_for(nonlocal_text["value"]))

                    _, resp = await worker.send_prompt(
                        prompt_text, max_tokens=256,
                        on_token=on_token,
                    )
                    accumulated = nonlocal_text["value"]
                self._pool_state = None
                break
            except PoolNotReadyError as nr:
                self._pool_state = nr.state
                msg = _POOL_STATE_TEXT.get(nr.state, "Loading")
                self._console.print(
                    Text(f"{INDENT}{msg}…", style="dim italic"),
                )
                await asyncio.sleep(2.0)
                continue
            except RuntimeError as e:
                self._console.print(
                    Text(f"{INDENT}{e}", style="red"),
                )
                self._console.print()
                return

        # Per-turn stats line below the answer. Same dim style as Claude
        # Code's trailing metadata.
        decode_tokens = int(resp.get("decode_tokens", 0))
        decode_s = float(resp.get("decode_ms", 0)) / 1000.0
        if decode_tokens > 0 and decode_s > 0:
            self._latest_tps = decode_tokens / decode_s
            self._console.print(
                Text(f"{INDENT}{self._latest_tps:.1f} tok/s · "
                     f"{decode_tokens} tokens in {decode_s:.1f}s",
                     style="dim"),
            )
        self._console.print()

"""Worker: connects to a dots hub and serves the compute side of inference.

This module is intentionally UI-free. A `Worker` knows how to:

  • Open a TCP connection to the hub
  • Send HELLO (carrying its persistent identity) and receive the welcome
  • Handle compute messages from the hub:
        LOAD_SHARD          → build a `Shard` for the assigned layer range
        COMPUTE             → run our layer slice on a hidden state
        RESET_CONVERSATION  → clear KV cache for a conv id
  • Send PROMPT requests and stream the response back to a caller

Anything terminal-y (the chat REPL, banner colors, scrolling, etc.) lives in
`dots.chat`. That lets a node donate compute without opening a chat surface
at all — `Worker.run_headless()` — and lets the same `Worker` underpin an
interactive `ChatUI` when the user does want to talk to the model.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Callable

from . import ui
from .peer import PeerConnection
from .protocol import decode_array, encode_array
from .shard import Shard, ShardSpec
from .util import free_ram_gb, load_or_create_identity

log = logging.getLogger("dots.worker")


class Worker:
    """Compute participant in a dots pool. UI-free by design."""

    def __init__(
        self,
        hub_host: str,
        hub_port: int,
        one_shot: str | None = None,
        max_tokens: int = 128,
        headless: bool = False,
        identity: str | None = None,
    ):
        self.hub_host = hub_host
        self.hub_port = hub_port
        self.one_shot = one_shot
        self.max_tokens = max_tokens
        self.headless = headless
        # Stable UUID that survives reconnects. Tests pass an explicit one
        # so multiple workers in the same process don't share the on-disk
        # ~/.dots/identity file.
        self.identity = identity if identity is not None else load_or_create_identity()

        self.conn: PeerConnection | None = None
        self.shard: Shard | None = None
        self.conv_id: str | None = None        # assigned by hub via WELCOME
        self.model_id: str | None = None
        self.n_layers: int | None = None

        # LOAD_SHARD vs COMPUTE must not interleave — a re-plan can swap the
        # shard while a stale COMPUTE is still in flight otherwise.
        self._shard_lock = asyncio.Lock()
        # The hub sends WELCOME after registering us and broadcasting
        # LOAD_SHARD; runners wait on this before considering us "ready".
        self._welcome = asyncio.Event()
        # When set, every TOKEN notification fan-outs through this callback
        # in addition to whatever capture buffer send_prompt installed.
        self._token_callback: Callable[[str], None] | None = None

    # ─────────────────────────────── lifecycle ──────────────────────────

    async def start(self) -> None:
        """High-level entry point used by the CLI: dispatches to the right
        runner based on the flags passed at construction."""
        await self.connect()

        if self.one_shot is not None:
            await self._run_one_shot()
            return
        if self.headless:
            await self._run_headless()
            return

        # Default: open the interactive chat surface on top of this worker.
        # Import lazily so the headless path stays cheap (no prompt_toolkit
        # imports unless the user asked for chat).
        from .chat import ChatUI
        await self._run_with_chat(ChatUI(self))

    async def connect(self) -> None:
        """Open TCP, send HELLO, wait for the hub's WELCOME."""
        ui.info(f"connecting to hub at {self.hub_host}:{self.hub_port}…")
        reader, writer = await asyncio.open_connection(self.hub_host, self.hub_port)
        self.conn = PeerConnection(
            reader, writer, f"{self.hub_host}:{self.hub_port}", self._handle,
        )
        ack = await self.conn.call(
            "hello",
            worker_id=self.identity,
            free_ram_gb=free_ram_gb(),
        )
        if ack.get("kind") != "hello_ack":
            raise RuntimeError(f"unexpected HELLO response: {ack}")
        self.model_id = ack.get("model_id")
        self.n_layers = ack.get("n_layers")
        ui.info(f"hub accepted; model={self.model_id} ({self.n_layers} layers)")
        await self._welcome.wait()

    async def wait_until_disconnected(self) -> None:
        if self.conn is not None:
            await self.conn._closed.wait()

    # ─────────────────────────── runners ────────────────────────────────

    async def _run_headless(self) -> None:
        short = self.identity[:8]
        # If the shard hasn't finished loading yet, that's fine — just show
        # what we know. The first COMPUTE will arrive after it's ready.
        layer_str = (
            f"layers {self.shard.spec.layer_start}:{self.shard.spec.layer_end}"
            if self.shard is not None else "no shard assigned yet"
        )
        ui.console.print(
            f"\n[bold magenta]···[/bold magenta]  "
            f"[bold]dots[/bold]  "
            f"[dim]worker {short} · {layer_str}[/dim]\n"
        )
        ui.console.print("[dim]ctrl-c to disconnect[/dim]\n")
        try:
            await self.wait_until_disconnected()
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        ui.notice("disconnected")

    async def _run_one_shot(self) -> None:
        hub_addr = f"{self.hub_host}:{self.hub_port}"
        # Give the shard a moment to load if it isn't ready yet.
        for _ in range(50):
            if self.shard is not None:
                break
            await asyncio.sleep(0.1)
        layer_range = (
            (self.shard.spec.layer_start, self.shard.spec.layer_end)
            if self.shard is not None else None
        )

        ui.console.print()
        ui.banner(self.model_id, hub_addr, layer_range)
        ui.console.print(f"\n  › {self.one_shot}\n")
        try:
            _, resp = await self.send_prompt(
                self.one_shot,
                max_tokens=self.max_tokens,
                on_token=lambda t: (sys.stdout.write(t), sys.stdout.flush()),
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

    async def _run_with_chat(self, chat: object) -> None:
        """Run a ChatUI (or any object with an async `run()`) alongside the
        worker's hub connection, exiting whichever finishes first."""
        repl_task = asyncio.create_task(chat.run())              # type: ignore[attr-defined]
        closed_task = asyncio.create_task(self.wait_until_disconnected())
        done, pending = await asyncio.wait(
            {repl_task, closed_task}, return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        if closed_task in done:
            ui.notice("hub disconnected — exiting")

    # ─────────────────────────── public chat API ────────────────────────

    async def send_prompt(
        self,
        text: str,
        max_tokens: int = 128,
        temperature: float | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> tuple[str, dict]:
        """Send one prompt; return `(response_text, stats_dict)`.

        Streamed token chunks always accumulate into the returned string.
        If `on_token` is given, it's also invoked for each chunk as it
        arrives — used by the interactive UI to route tokens into its
        history buffer in real time.
        """
        if self.conn is None:
            raise RuntimeError("not connected to hub")
        payload: dict = {"text": text, "max_tokens": max_tokens}
        if temperature is not None:
            payload["temperature"] = temperature
        captured: list[str] = []

        def hook(chunk: str) -> None:
            captured.append(chunk)
            if on_token is not None:
                on_token(chunk)

        prev = self._token_callback
        self._token_callback = hook
        try:
            resp = await self.conn.call("prompt", **payload)
        finally:
            self._token_callback = prev
        if resp.get("kind") != "turn_done":
            raise RuntimeError(resp.get("error", "turn failed"))
        return "".join(captured), resp

    # ─────────────────────────── hub→worker handler ─────────────────────

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
            # Notification — route through whichever consumer is installed
            # by send_prompt; fall back to stdout (CLI / debugging).
            text = msg.get("text", "")
            if self._token_callback is not None:
                try:
                    self._token_callback(text)
                except Exception:
                    log.exception("token callback failed")
            else:
                sys.stdout.write(text)
                sys.stdout.flush()
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
            self.shard = None
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

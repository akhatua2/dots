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

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style

from . import ui
from .peer import PeerConnection
from .protocol import decode_array, encode_array
from .shard import Shard, ShardSpec
from .util import free_ram_gb

log = logging.getLogger("dots.worker")

_PROMPT_STYLE = Style.from_dict({"prompt": "ansimagenta bold", "": "ansiwhite"})


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
            # Notification — write straight to stdout, no response.
            text = msg.get("text", "")
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
        if self.one_shot is not None:
            ui.console.print()
            ui.banner(self.model_id, [f"hub {self.hub_host}:{self.hub_port}"])
            ui.console.print(f"[dim]one-shot: {self.one_shot}[/dim]\n")
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

        session = PromptSession()
        ui.console.print()
        ui.banner(self.model_id, [f"hub {self.hub_host}:{self.hub_port}"])
        ui.console.print(
            "[dim]/reset clear conversation · Ctrl-D to leave the pool[/dim]\n"
        )

        while True:
            try:
                line = await session.prompt_async(
                    HTML("<prompt>❯ </prompt>"), style=_PROMPT_STYLE
                )
            except (EOFError, KeyboardInterrupt):
                ui.console.print()
                return
            line = line.strip()
            if not line:
                continue
            if line == "/reset":
                resp = await self.conn.call("reset")
                if resp.get("kind") == "ok":
                    ui.info("conversation reset")
                else:
                    ui.error(resp.get("error", "reset failed"))
                continue

            try:
                _, resp = await self.send_prompt(line, max_tokens=256)
                sys.stdout.write("\n")
                sys.stdout.flush()
                ui.stats(
                    int(resp.get("prefill_tokens", 0)),
                    float(resp.get("prefill_ms", 0)),
                    int(resp.get("decode_tokens", 0)),
                    float(resp.get("decode_ms", 0)) / 1000.0,
                )
            except RuntimeError as e:
                ui.error(str(e))
            ui.console.print()

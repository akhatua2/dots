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
from .inference import FullModelHost
from .partial_loader import load_partial
from .peer import PeerConnection
from .pool import resource_decl_to_hello_field
from .scheduler import ResourceDecl
from .util import (
    clear_worker_pid,
    effective_tflops_estimate,
    free_ram_gb,
    load_or_create_account,
    load_or_create_identity,
    save_last_hub,
    supported_backends,
    write_worker_pid,
)

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
        max_load_gb: float | None = None,
        compute: bool = True,
        account_id: str | None = None,
        account_alias: str | None = None,
    ):
        self.hub_host = hub_host
        self.hub_port = hub_port
        self.one_shot = one_shot
        self.max_tokens = max_tokens
        self.headless = headless
        # User-set cap on how much RAM this worker offers the pool.
        # None ⇒ no cap (report actual free RAM). Setting this is the
        # "I'll only pool N GB of my machine" knob — it caps the
        # ram_gb_free value we send to the hub, so placement decisions
        # never assign more than the user agreed to.
        self.max_load_gb = max_load_gb
        # Whether this Worker contributes compute to the pool. False
        # makes it a pure chat client: the hub won't send `load_units`
        # and the worker won't load a model.
        self.compute = compute
        # Stable UUID that survives reconnects. Tests pass an explicit one
        # so multiple workers in the same process don't share the on-disk
        # ~/.dots/identity file.
        self.identity = identity if identity is not None else load_or_create_identity()
        # Human-owner account: who earns/spends CU. Persisted across
        # devices (copy ~/.dots/account to attribute multiple workers to
        # the same account). Tests pass an explicit one to avoid touching
        # the real ~/.dots/account file.
        self.account_id = account_id if account_id is not None else load_or_create_account()
        # Cosmetic short handle shown on the hub side. May be None.
        from .util import load_account_alias
        self.account_alias = (
            account_alias if account_alias is not None else load_account_alias()
        )

        self.conn: PeerConnection | None = None
        # Compute workers host the full model and serve `delegate_turn`
        # requests from the hub. Chat-only clients (compute=False) never
        # populate this — they just send prompts and receive token
        # notifications. The host is loaded asynchronously after WELCOME
        # so the hub can register us as joined before model load completes.
        self.host: FullModelHost | None = None
        self._host_ready = asyncio.Event()
        self._host_load_task: asyncio.Task | None = None
        # Peer server: compute workers also listen for incoming peer
        # connections so the hub can ask them to probe each other (and so
        # the future multi-worker pipeline can pass hidden states without
        # routing through the hub).
        self._peer_server: asyncio.Server | None = None
        self._peer_listen_port: int | None = None
        # Outgoing connections to chain peers (only populated when we're
        # the head worker in a multi-worker placement). Keyed by peer
        # worker_id; cached so the per-token hot path doesn't reconnect.
        self._peer_conns: dict[str, PeerConnection] = {}
        # Per-conversation handler lock: two delegate_turn calls for the
        # same conv would corrupt the KV cache.
        self._turn_locks: dict[str, asyncio.Lock] = {}

        self.conv_id: str | None = None        # assigned by hub via WELCOME
        self.model_id: str | None = None
        self.n_layers: int | None = None

        # The hub sends WELCOME after registering us; runners wait on this
        # before considering us "ready".
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
        # Compute workers stand up a small peer-listen server before they
        # phone the hub, so they can be probed and (in the future) be a
        # target of worker-to-worker `compute_unit` calls.
        if self.compute and self._peer_server is None:
            await self._start_peer_server()

        ui.info(f"connecting to hub at {self.hub_host}:{self.hub_port}…")
        reader, writer = await asyncio.open_connection(self.hub_host, self.hub_port)
        self.conn = PeerConnection(
            reader, writer, f"{self.hub_host}:{self.hub_port}", self._handle,
        )
        # Build the worker's resource declaration. The user-set cap, if
        # any, is applied here so the hub never sees more RAM than the
        # user agreed to pool. `effective_tflops_estimate` is per-chip
        # (override via DOTS_TFLOPS).
        reported_ram = free_ram_gb()
        if self.max_load_gb is not None:
            reported_ram = min(reported_ram, float(self.max_load_gb))
        decl = ResourceDecl(
            worker_id=self.identity,
            account_id=self.account_id,
            ram_gb_free=reported_ram,
            compute_tflops_per_sec=effective_tflops_estimate(),
            backends=supported_backends(),
            cluster_id="",
        )
        hello_payload: dict = {
            "worker_id": self.identity,
            "account_id": self.account_id,
            "compute": bool(self.compute),
            "resources": resource_decl_to_hello_field(decl),
        }
        if self._peer_listen_port is not None:
            hello_payload["peer_listen_port"] = self._peer_listen_port
        if self.account_alias:
            hello_payload["account_alias"] = self.account_alias
        ack = await self.conn.call("hello", **hello_payload)
        if ack.get("kind") != "hello_ack":
            raise RuntimeError(f"unexpected HELLO response: {ack}")
        self.model_id = ack.get("model_id")
        self.n_layers = ack.get("n_layers")
        ui.info(f"hub accepted; model={self.model_id} ({self.n_layers} layers)")
        # Persist this hub address so the next `dots chat` / `dots worker`
        # invocation can default to it when the user omits the argument.
        try:
            save_last_hub(f"{self.hub_host}:{self.hub_port}")
        except Exception:
            log.debug("failed to persist last_hub", exc_info=True)

        # Compute workers used to auto-load the full model right here.
        # Now they wait for the hub to tell them what layer range (and
        # head modules) to load via a `load_units` notification. The
        # _host_load_task is kicked off inside `_handle_load_units`.
        await self._welcome.wait()

    # ────────────────────── peer-server lifecycle ───────────────────────

    async def _start_peer_server(self) -> None:
        """Bind a small TCP server for incoming peer connections.

        The OS picks a free port. We bind to all interfaces so peers on
        the same LAN / tailnet can reach us. The hub learns this port
        from HELLO's `peer_listen_port` and tells other workers to use
        it when probing.
        """
        self._peer_server = await asyncio.start_server(
            self._on_peer_connect, host="0.0.0.0", port=0,
        )
        sock = self._peer_server.sockets[0]
        self._peer_listen_port = sock.getsockname()[1]
        log.info("peer server listening on port %d", self._peer_listen_port)

    async def _on_peer_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        addr = writer.get_extra_info("peername")
        addr_str = f"{addr[0]}:{addr[1]}" if addr else "?"
        conn = PeerConnection(reader, writer, addr_str, self._handle_peer_msg)
        try:
            await conn._closed.wait()
        finally:
            try:
                await conn.close()
            except Exception:
                pass

    async def _handle_peer_msg(self, msg: dict) -> dict | None:
        """Dispatcher for messages arriving on the peer server.

        Strict allowlist: peer connections may only request data-plane
        operations. Control-plane ops (`load_unit`, `shutdown`, etc.)
        are reserved for the hub channel (see [[worker-security-model]]).
        """
        kind = msg.get("kind")
        if kind == "peer_ping":
            # Hot-path latency primitive; just ack with the source ts so
            # the caller can compute RTT locally.
            return {"kind": "peer_pong", "ts_ns": msg.get("ts_ns")}
        if kind == "compute_unit":
            return await self._handle_compute_unit(msg)
        return {"kind": "error", "error": f"unknown peer kind: {kind!r}"}

    async def _handle_compute_unit(self, msg: dict) -> dict:
        """Run a single hidden-state forward through this worker's
        assigned layer range. Used by head workers chaining work
        through multi-worker placements."""
        from .protocol import decode_array, encode_array
        try:
            await asyncio.wait_for(self._host_ready.wait(), timeout=120.0)
        except asyncio.TimeoutError:
            return {"kind": "error", "error": "model not loaded yet"}
        conv_id = msg["conv_id"]
        start = int(msg["layer_start"])
        end = int(msg["layer_end"])
        hidden_in = decode_array(msg["hidden"])
        assert self.host is not None
        try:
            hidden_out = self.host.forward_segment(conv_id, hidden_in, start, end)
        except Exception as e:
            log.exception("compute_unit failed")
            return {"kind": "error", "error": str(e)}
        return {
            "kind": "compute_unit_out",
            "hidden": encode_array(hidden_out),
        }

    async def _get_or_open_peer(self, peer_id: str, host: str, port: int) -> PeerConnection:
        """Cached outgoing connection to a peer worker. Workers in the
        head role open one of these per downstream chain member and reuse
        it for every hidden-state hop across all tokens."""
        existing = self._peer_conns.get(peer_id)
        if existing is not None and not existing._closed.is_set():
            return existing
        reader, writer = await asyncio.open_connection(host, port)
        # Peer-initiated connections only RECEIVE responses, never serve
        # requests, so the handler is a no-op.
        conn = PeerConnection(
            reader, writer, f"{host}:{port}", lambda _m: None,
        )
        self._peer_conns[peer_id] = conn
        return conn

    async def _load_host_async(
        self,
        model_id: str,
        layer_start: int = 0,
        layer_end: int | None = None,
        needs_head: bool = True,
    ) -> None:
        """Background task: load the assigned slice into a FullModelHost.

        With partial loading, this worker only materializes the assigned
        decoder layers + (if head) embed/norm/lm_head. Unowned layers
        are kept on disk (mmap'd safetensors) so peak + steady-state RAM
        stay bounded to the assignment.
        """
        slice_descr = (
            f"layers [{layer_start}:{layer_end or '?'}]"
            + (" + head" if needs_head else "")
        )
        ui.info(f"loading {model_id} {slice_descr} on worker…")
        try:
            model, tokenizer, n_total = await asyncio.to_thread(
                load_partial, model_id,
                layer_start=layer_start,
                layer_end=layer_end,
                needs_head=needs_head,
            )
        except Exception as e:
            ui.error(f"model load failed on worker: {e}")
            raise
        self.host = FullModelHost(model, tokenizer)
        self.n_layers = n_total
        self._host_ready.set()
        ui.info(f"worker ready · {self.host.n_layers} layers · "
                f"{self.host.active_params / 1e9:.2f}B active params")
        # Tell the hub we're loaded and ready to serve turns.
        # Sniff geometry from the live model so the hub can build the
        # work-unit graph for scheduler-driven placement decisions.
        from .work_units import geometry_from_mlx_model
        geom = geometry_from_mlx_model(model)
        geom_payload = (
            {"hidden_size": geom.hidden_size,
             "vocab_size": geom.vocab_size,
             "n_layers": geom.n_layers}
            if geom else None
        )

        if self.conn is not None:
            try:
                await self.conn.notify(
                    "worker_ready",
                    worker_id=self.identity,
                    n_layers=self.host.n_layers,
                    active_params=self.host.active_params,
                    geometry=geom_payload,
                )
            except Exception:
                log.debug("could not notify hub of worker_ready", exc_info=True)

    async def wait_until_disconnected(self) -> None:
        if self.conn is not None:
            await self.conn._closed.wait()

    # ─────────────────────────── runners ────────────────────────────────

    async def _run_headless(self) -> None:
        short = self.identity[:8]
        # If the host model hasn't finished loading yet, just show what we
        # know. Delegated turns wait on _host_ready before serving.
        status = (
            f"{self.host.n_layers} layers loaded"
            if self.host is not None else "loading…"
        )
        ui.console.print(
            f"\n[bold magenta]···[/bold magenta]  "
            f"[bold]dots[/bold]  "
            f"[dim]worker {short} · {status}[/dim]\n"
        )
        ui.console.print("[dim]ctrl-c to disconnect[/dim]\n")
        try:
            await self.wait_until_disconnected()
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        ui.notice("disconnected")

    async def _run_one_shot(self) -> None:
        hub_addr = f"{self.hub_host}:{self.hub_port}"
        # Wait briefly for the host to load if we're also a compute worker;
        # otherwise we're a chat-only client and there's nothing to wait for.
        if self.compute:
            for _ in range(50):
                if self._host_ready.is_set():
                    break
                await asyncio.sleep(0.1)

        ui.console.print()
        ui.banner(self.model_id, hub_addr, None)
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
        if kind == "load_units":
            return await self._handle_load_units(msg)
        if kind == "delegate_turn":
            return await self._handle_delegate_turn(msg)
        if kind == "probe_peer":
            return await self._handle_probe_peer(msg)
        if kind == "reset_conversation":
            conv_id = msg.get("conv_id")
            if conv_id and self.host is not None:
                self.host.reset_conversation(conv_id)
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

    async def _handle_load_units(self, msg: dict) -> dict | None:
        """Hub-driven model load. Idempotent — re-issuing with the same
        assignment is a no-op; re-issuing with a different assignment
        kicks off a fresh load (the previous host is replaced)."""
        if not self.compute:
            return None
        model_id = msg.get("model_id") or self.model_id
        layer_start = int(msg.get("layer_start", 0))
        layer_end_raw = msg.get("layer_end")
        layer_end = int(layer_end_raw) if layer_end_raw is not None else None
        needs_head = bool(msg.get("needs_head", True))
        # Already loaded with this exact assignment? Nothing to do.
        if (self.host is not None
                and self.model_id == model_id
                and self._host_ready.is_set()):
            return None
        self.model_id = model_id
        self._host_ready.clear()
        self._host_load_task = asyncio.create_task(
            self._load_host_async(
                model_id, layer_start, layer_end, needs_head,
            )
        )
        return None

    async def _handle_delegate_turn(self, msg: dict) -> dict:
        """The hub delegates an entire chat turn to us.

        Runs the inference loop locally on our `FullModelHost`, streaming
        each generated token text fragment back to the hub via
        `delegate_token` notifications. Returns `delegate_done` with
        prefill / decode counts so the hub can attribute the FLOP-CU.
        """
        if not self.compute:
            return {"kind": "error",
                    "error": "this client doesn't host compute"}

        # Wait for the model to finish loading. The hub may call us right
        # after HELLO; the load runs in background.
        try:
            await asyncio.wait_for(self._host_ready.wait(), timeout=120.0)
        except asyncio.TimeoutError:
            return {"kind": "error", "error": "model not loaded yet"}

        conv_id = msg["conv_id"]
        history = msg["history"]
        max_tokens = int(msg.get("max_tokens", 256))
        temperature = float(msg.get("temperature", 0.7))
        # Optional multi-worker chain spec. Empty / missing = single-worker
        # delegation (we run the whole model). Present = we're the head;
        # each chain entry tells us where to send hidden states for the
        # layers we don't own.
        chain = msg.get("chain") or []
        my_layer_end = int(msg.get("my_layer_end", self.host.n_layers if self.host else 0))

        # One turn per conv at a time. Two interleaved decode loops on the
        # same KV cache would corrupt it.
        lock = self._turn_locks.setdefault(conv_id, asyncio.Lock())

        async def on_token(text: str) -> None:
            if self.conn is None:
                return
            try:
                await self.conn.notify(
                    "delegate_token", conv_id=conv_id, text=text,
                )
            except Exception:
                log.debug("delegate_token notify failed", exc_info=True)

        async with lock:
            assert self.host is not None
            try:
                if chain:
                    text, stats = await self._run_chain_turn(
                        conv_id=conv_id, history=history,
                        max_tokens=max_tokens, temperature=temperature,
                        on_token=on_token,
                        my_layer_end=my_layer_end, chain=chain,
                    )
                else:
                    text, stats = await self.host.run_turn(
                        conv_id=conv_id,
                        history=history,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        on_token=on_token,
                    )
            except Exception as e:
                log.exception("delegate_turn failed")
                return {"kind": "error", "error": str(e)}

        return {
            "kind": "delegate_done",
            "text": text,
            "active_params": self.host.active_params,
            **stats,
        }

    async def _run_chain_turn(
        self,
        *,
        conv_id: str,
        history: list,
        max_tokens: int,
        temperature: float,
        on_token,
        my_layer_end: int,
        chain: list,
    ) -> tuple[str, dict]:
        """Generate a turn across multiple workers.

        We are the head: we own embed, layers [0, my_layer_end), final
        norm, lm_head, sample. Each `chain` entry tells us a downstream
        worker, the layer range they own, and how to reach them. Hidden
        state flows head → chain[0] → chain[1] → ... → back to us for
        sampling.
        """
        import time as _time
        import mlx.core as mx
        from .inference import _sample, _stop_ids
        from .protocol import decode_array, encode_array

        host = self.host
        assert host is not None

        # Pre-open one connection per chain peer; reuse across the
        # entire turn so per-token latency is just one round-trip.
        peer_conns: list[tuple[PeerConnection, int, int]] = []
        for entry in chain:
            peer_id = str(entry["worker_id"])
            host_addr = str(entry["host"])
            port = int(entry["port"])
            start = int(entry["layer_start"])
            end = int(entry["layer_end"])
            conn = await self._get_or_open_peer(peer_id, host_addr, port)
            peer_conns.append((conn, start, end))

        async def forward_through_chain(hidden):
            # Our local segment first.
            hidden = host.forward_segment(conv_id, hidden, 0, my_layer_end)
            # Then each downstream chain segment.
            for conn, start, end in peer_conns:
                resp = await conn.call(
                    "compute_unit",
                    conv_id=conv_id,
                    layer_start=start, layer_end=end,
                    hidden=encode_array(hidden),
                )
                if resp.get("kind") != "compute_unit_out":
                    raise RuntimeError(
                        f"compute_unit failed: {resp.get('error', resp)}"
                    )
                hidden = decode_array(resp["hidden"])
            return hidden

        cache = host._get_cache(conv_id)
        full_ids = host.tokenizer.apply_chat_template(
            history, add_generation_prompt=True,
        )
        seen = host._token_counts.get(conv_id, 0)
        if seen > len(full_ids):
            host.reset_conversation(conv_id)
            cache = host._get_cache(conv_id)
            seen = 0
        new_ids = full_ids[seen:]
        if not new_ids:
            return "", {"prefill_tokens": 0, "decode_tokens": 0,
                        "prefill_ms": 0.0, "elapsed_ms": 0.0}

        # Prefill: embed → chain → norm + lm_head → sample first token.
        t0 = _time.perf_counter()
        hidden = host.model.model.embed_tokens(mx.array([new_ids]))
        hidden = await forward_through_chain(hidden)
        hidden = host.model.model.norm(hidden)
        logits = host._project_logits(hidden[:, -1:, :])
        token = _sample(logits[0, -1], temperature)
        prefill_tokens = len(new_ids)
        prefill_ms = (_time.perf_counter() - t0) * 1000
        host._token_counts[conv_id] = seen + len(new_ids)

        stop = _stop_ids(host.tokenizer)
        response_tokens: list[int] = []
        decode_start = _time.perf_counter()
        while len(response_tokens) < max_tokens and token not in stop:
            text = host.tokenizer.decode([token])
            await on_token(text)
            response_tokens.append(token)
            hidden = host.model.model.embed_tokens(mx.array([[token]]))
            hidden = await forward_through_chain(hidden)
            hidden = host.model.model.norm(hidden)
            logits = host._project_logits(hidden[:, -1:, :])
            token = _sample(logits[0, -1], temperature)
            host._token_counts[conv_id] += 1

        elapsed_ms = (_time.perf_counter() - decode_start) * 1000
        text = host.tokenizer.decode(response_tokens)
        return text, {
            "prefill_tokens": prefill_tokens,
            "decode_tokens": len(response_tokens),
            "prefill_ms": prefill_ms,
            "elapsed_ms": elapsed_ms,
        }

    async def _handle_probe_peer(self, msg: dict) -> dict:
        """Hub-triggered probe: open a connection to the target peer,
        time N pings, return the RTT statistics to the hub."""
        import time as _time
        target_id = msg.get("target_id")
        target_host = msg.get("target_host")
        target_port = msg.get("target_port")
        samples = int(msg.get("samples", 3))
        if not target_host or not target_port:
            return {"kind": "probe_result", "target_id": target_id,
                    "error": "missing target host/port"}
        rtts: list[float] = []
        try:
            reader, writer = await asyncio.open_connection(target_host, target_port)
            conn = PeerConnection(
                reader, writer,
                f"{target_host}:{target_port}",
                lambda _m: None,
            )
            for _ in range(samples):
                t0 = _time.perf_counter_ns()
                try:
                    resp = await asyncio.wait_for(
                        conn.call("peer_ping", ts_ns=t0), timeout=2.0,
                    )
                except asyncio.TimeoutError:
                    continue
                if resp.get("kind") != "peer_pong":
                    continue
                rtt_ms = (_time.perf_counter_ns() - t0) / 1e6
                rtts.append(rtt_ms)
            await conn.close()
        except Exception as e:
            return {"kind": "probe_result", "target_id": target_id,
                    "error": str(e)}
        if not rtts:
            return {"kind": "probe_result", "target_id": target_id,
                    "error": "no successful pings"}
        rtts.sort()
        return {
            "kind": "probe_result",
            "target_id": target_id,
            "rtt_p50_ms": rtts[len(rtts) // 2],
            "rtt_p99_ms": rtts[-1],
            "samples": len(rtts),
        }


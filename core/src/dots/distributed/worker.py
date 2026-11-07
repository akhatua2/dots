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
import base64
import binascii
import logging
import os
import sys
import time
import uuid
from typing import Callable

from .. import ui
from ..backends.base import BackendRuntime, ModelAssignment
from ..backends.registry import get_backend_runtime
from ..backends.mlx.draft import draft_for, load_draft
from ..generation.base import GenerationRequest
from ..generation.registry import get_generation_strategy
from ..generation.speculative import SpeculativeGeneration
from ..image_tools import runners as image_runners
from ..images import ImageJob
from .chain_timing import ChainTiming
from .peer import PeerConnection
from .iroh_net import IrohEndpoint, IrohStreamTransport, load_or_create_secret_key
from .pool import resource_decl_to_hello_field
from ..placement.scheduler import ResourceDecl
from ..system.geo import lookup_coarse_geo
from ..system.util import (
    detect_device,
    load_or_create_account,
    load_or_create_identity,
    save_last_hub,
)
from ..system.resources import discover_worker_limits

log = logging.getLogger("dots.worker")


async def _ignore_inbound(_msg: dict) -> None:
    """Handler for outgoing-only peer channels: they only receive responses to
    our own calls, never inbound requests, so nothing to serve."""
    return None


class PoolNotReadyError(RuntimeError):
    """Raised by `Worker.send_prompt` when the hub reports the pool is
    not yet ready to serve. Carries a `state` ∈ {"loading", "waiting"}
    so the UI can pick a friendly message and decide to retry."""

    def __init__(self, state: str):
        super().__init__(state)
        self.state = state


class Worker:
    """Compute participant in a dots pool. UI-free by design."""

    def __init__(
        self,
        hub_ticket: str,
        one_shot: str | None = None,
        max_tokens: int = 128,
        headless: bool = False,
        identity: str | None = None,
        max_load_gb: float | None = None,
        compute: bool = True,
        account_id: str | None = None,
        account_alias: str | None = None,
        requested_model_id: str | None = None,
        pool: str = "",
    ):
        # The hub's shareable iroh ticket — the "join token". The worker dials
        # this over iroh (NAT-traversed) rather than a host:port.
        self.hub_ticket = hub_ticket
        self.one_shot = one_shot
        self.max_tokens = max_tokens
        self.headless = headless
        # User-set cap on how much RAM this worker offers the pool.
        # None ⇒ no cap (report actual free RAM). Setting this is the
        # "I'll only pool N GB of my machine" knob — it caps the
        # ram_gb_free value we send to the hub, so placement decisions
        # never assign more than the user agreed to.
        self.max_load_gb = max_load_gb
        # The pool this worker lends to: the hub runs only that pool's model on it. "" is the hub's default pool.
        self.pool = pool
        self.resource_limits = discover_worker_limits(max_ram_gb=self.max_load_gb)
        # Whether this Worker contributes compute to the pool. False
        # makes it a pure chat client: the hub won't send `load_units`
        # and the worker won't load a model.
        self.compute = compute
        # Stable UUID that survives reconnects. Tests pass an explicit one
        # so multiple workers in the same process don't share the on-disk
        # ~/.dots/identity file.
        self.identity = identity if identity is not None else load_or_create_identity(pool)
        # Human-owner account: who earns/spends CU. Persisted across
        # devices (copy ~/.dots/account to attribute multiple workers to
        # the same account). Tests pass an explicit one to avoid touching
        # the real ~/.dots/account file.
        self.account_id = (
            account_id if account_id is not None else load_or_create_account()
        )
        # Cosmetic short handle shown on the hub side. May be None.
        from ..system.util import load_account_alias

        self.account_alias = (
            account_alias if account_alias is not None else load_account_alias()
        )

        self.conn: PeerConnection | None = None
        # Compute workers own a backend runtime. The MLX implementation
        # currently wraps the loaded model host; future backends plug in
        # here without changing worker control flow.
        self.backend: BackendRuntime | None = None
        self.backends: dict[str, BackendRuntime] = {}
        self.generation = get_generation_strategy()
        # Drafts for speculative decoding, by (draft repo, model): a model's own head or a small model
        # (None: it failed to load)
        self._drafts: dict[tuple[str, str], object | None] = {}
        # How this Mac draws each family of image model it can (image_tools/runners.toml); one picture at a time
        self.runners = image_runners.here()
        self._drawing = asyncio.Lock()
        self._host_ready = asyncio.Event()
        self._host_ready_by_model: dict[str, asyncio.Event] = {}
        self._host_load_task: asyncio.Task | None = None
        self._host_load_tasks: dict[str, asyncio.Task] = {}
        # Exact assignment that produced the currently-loaded host. Lets
        # `_handle_load_units` distinguish "same instruction we already
        # served" from "hub re-partitioned, need to re-load with new
        # range/head".
        self._loaded_assignment: tuple[str, str, int, int | None, bool] | None = None
        self._loaded_assignments: dict[str, tuple[str, str, int, int | None, bool]] = {}
        # The worker's own iroh node. It serves two roles: dialing the hub,
        # and accepting incoming peer connections from other workers (so the
        # multi-worker pipeline can pass hidden states directly, peer-to-peer).
        # Its ticket is advertised to the hub in HELLO as `peer_ticket`.
        self._endpoint: IrohEndpoint | None = None
        self._peer_ticket: str | None = None
        # Outgoing connections to chain peers (only populated when we're
        # the head worker in a multi-worker placement). Keyed by peer
        # worker_id; cached so the per-token hot path doesn't reconnect.
        self._peer_conns: dict[str, PeerConnection] = {}
        # Passes sent round a ring, waiting for the hidden state to come home
        self._ring_waiters: dict[str, asyncio.Future] = {}
        # Hidden states for one conversation are run one at a time, in the order they arrived: a prompt
        # sent in chunks must reach the cache in order. `_next_chunk` is the chunk each expects next.
        self._segment_locks: dict[str, asyncio.Lock] = {}
        self._next_chunk: dict[str, int] = {}
        # Per-conversation handler lock: two delegate_turn calls for the
        # same conv would corrupt the KV cache.
        self._turn_locks: dict[str, asyncio.Lock] = {}

        self.conv_id: str | None = None  # assigned by hub via WELCOME
        self.model_id: str | None = None
        self.requested_model_id = requested_model_id
        self.n_layers: int | None = None

        # The hub sends WELCOME after registering us; runners wait on this
        # before considering us "ready".
        self._welcome = asyncio.Event()
        # When set, every TOKEN notification fan-outs through this callback
        # in addition to whatever capture buffer send_prompt installed.
        self._token_callback: Callable[[str], None] | None = None

    @property
    def host(self):
        """Compatibility view of the MLX host, if the active backend has one."""
        if self.backend is None:
            return None
        return getattr(self.backend, "host", None)

    def _ready_event_for(self, model_id: str) -> asyncio.Event:
        event = self._host_ready_by_model.get(model_id)
        if event is None:
            event = asyncio.Event()
            self._host_ready_by_model[model_id] = event
        return event

    def _runtime_for_model(self, model_id: str) -> BackendRuntime | None:
        return self.backends.get(model_id)

    # ─────────────────────────────── lifecycle ──────────────────────────

    async def start(self) -> None:
        """High-level entry point used by the CLI: dispatches to the right
        runner based on the flags passed at construction.

        We close the hub connection explicitly on exit so the hub sees a
        FIN immediately. Without this, dropping out via Ctrl-C / Ctrl-D
        left the StreamWriter to be torn down by asyncio's event-loop
        shutdown — which doesn't reliably flush a FIN — and the hub
        would keep the participant in its pool until TCP keepalive
        timed it out (minutes later).
        """
        await self.connect()
        try:
            if self.one_shot is not None:
                await self._run_one_shot()
                return
            if self.headless:
                await self._run_headless()
                return

            # Default: open the interactive chat surface on top of this
            # worker. Import lazily so the headless path stays cheap (no
            # prompt_toolkit imports unless the user asked for chat).
            from ..chat import ChatUI

            await self._run_with_chat(ChatUI(self))
        finally:
            await self._disconnect()

    async def _disconnect(self) -> None:
        """Tear down outgoing peer connections + the hub connection.

        Idempotent — safe to call from a `finally` even if `connect()`
        itself raised. Best-effort: swallows exceptions because we're on
        the exit path and have nothing useful to do with them.
        """
        for peer in list(self._peer_conns.values()):
            try:
                await peer.close()
            except Exception:
                pass
        self._peer_conns.clear()
        if self.conn is not None:
            try:
                await self.conn.close()
            except Exception:
                pass
            self.conn = None
        if self._endpoint is not None:
            try:
                await self._endpoint.shutdown()
            except Exception:
                pass
            self._endpoint = None

    async def connect(self) -> None:
        """Create our iroh node, dial the hub by ticket, HELLO, await WELCOME."""
        # The worker's iroh node does double duty: it dials the hub, and it
        # accepts inbound connections from other workers for the data plane.
        # A stable secret key keyed on our identity gives us a stable NodeId
        # (and ticket) across reconnects.
        if self._endpoint is None:
            self._endpoint = await IrohEndpoint.create(
                on_accept=self._serve_incoming_peer,
                secret_key=load_or_create_secret_key(f"worker-{self.identity}"),
            )
            if self.compute:
                self._peer_ticket = await self._endpoint.ticket()

        if self.compute and not self.resource_limits.backends:
            from ..backends import registry

            ui.notice("this Mac can't run models here: " + (registry.why_no_mlx or "MLX isn't usable"))
        if self.compute and self.pool:
            ui.info("draws " + (", ".join(sorted({r.name for r in self.runners})) or "nothing on this Mac"))
        ui.info("connecting to hub (iroh)…")
        self.conn = await self._endpoint.connect(self.hub_ticket, self._handle)
        # Build the worker's bounded resource declaration. Defaults are
        # conservative fractions of available machine resources; hard caps
        # come from CLI/env policy.
        decl = ResourceDecl(
            worker_id=self.identity,
            account_id=self.account_id,
            ram_gb_free=self.resource_limits.ram_gb,
            compute_tflops_per_sec=self.resource_limits.compute_tflops_per_sec,
            backends=self.resource_limits.backends,
            cluster_id="",
            disk_gb_free=self.resource_limits.disk_gb,
            cpu_utilization=self.resource_limits.cpu_utilization,
            io_read_gbps=self.resource_limits.io_read_gbps,
            io_write_gbps=self.resource_limits.io_write_gbps,
        )
        # Best-effort: advertise our coarse location + public IP so the hub can
        # show a map of the pool. Opt-out via DOTS_NO_GEO; any failure (offline,
        # slow) just omits it and never blocks joining. Runs off-loop so the
        # network call doesn't stall the event loop.
        geo = await asyncio.to_thread(lookup_coarse_geo)
        # Device type/name for the pool map (e.g. "MacBook Pro · Apple M1 Pro").
        # Off-loop like geo; best-effort, never blocks joining.
        device = await asyncio.to_thread(detect_device)
        hello_payload: dict = {
            "worker_id": self.identity,
            "pool": self.pool,
            "account_id": self.account_id,
            "compute": bool(self.compute),
            "resources": resource_decl_to_hello_field(decl),
        }
        if geo:
            hello_payload["geo"] = geo
        if device:
            hello_payload["device"] = device
        if self._peer_ticket is not None:
            # Our iroh ticket: how other workers dial us directly for the
            # data plane. The hub hands it to chain peers; iroh hole-punches
            # or relays, so it works across NATs with no routable IP.
            hello_payload["peer_ticket"] = self._peer_ticket
        join_token = os.environ.get("DOTS_JOIN_TOKEN")
        if join_token:
            # Present the shared join token if the operator set one; the hub
            # rejects the connection on mismatch.
            hello_payload["join_token"] = join_token
        if self.account_alias:
            hello_payload["account_alias"] = self.account_alias
        if self.runners and self.compute:
            # The originals it can draw models of; its pool says which model when it asks for a picture
            hello_payload["images"] = sorted({o for r in self.runners for o in r.originals})
        # A chat client tells the hub which model it intends to query so
        # the hub knows that model is "in use" and won't evict it to make
        # room for another. Compute-only workers leave this unset.
        if self.requested_model_id:
            hello_payload["requested_model_id"] = self.requested_model_id
        ack = await self.conn.call("hello", **hello_payload)
        if ack.get("kind") != "hello_ack":
            raise RuntimeError(f"unexpected HELLO response: {ack}")
        # The hub is model-agnostic — the ack carries no model. A chat
        # client supplies its own `requested_model_id`; a compute worker
        # leaves it unset and loads whatever the hub later assigns.
        ui.info(f"hub accepted (model: {self.requested_model_id or 'assigned by hub'})")
        # Persist this hub ticket so the next `dots chat` / `dots worker`
        # invocation can default to it when the user omits the argument.
        try:
            save_last_hub(self.hub_ticket)
        except Exception:
            log.debug("failed to persist last_hub", exc_info=True)

        # Compute workers used to auto-load the full model right here.
        # Now they wait for the hub to tell them what layer range (and
        # head modules) to load via a `load_units` notification. The
        # _host_load_task is kicked off inside `_handle_load_units`.
        await self._welcome.wait()

    # ────────────────────── incoming peer connections ───────────────────

    async def _serve_incoming_peer(
        self, transport: IrohStreamTransport, peer_id: str
    ) -> None:
        """Serve one inbound peer connection (another worker dialing us over
        iroh for the data plane) until it disconnects. Invoked by our iroh
        endpoint's ALPN listener."""
        conn = PeerConnection(transport, peer_id, self._handle_peer_msg)
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
        if kind == "trim_cache":
            return self._handle_trim_cache(msg)
        if kind == "ring_unit":
            await self._handle_ring_unit(msg)
            return None
        if kind == "ring_result":
            self._handle_ring_result(msg)
            return None
        return {"kind": "error", "error": f"unknown peer kind: {kind!r}"}

    def _handle_trim_cache(self, msg: dict) -> dict:
        """The head rejected some draft tokens: drop them from this worker's slice of the cache too."""
        runtime = self._runtime_for_model(msg.get("model_id") or self.model_id or "")
        if runtime is None:
            return {"kind": "error", "error": "backend not selected"}
        try:
            runtime.trim(str(msg["conv_id"]), int(msg["n"]))
        except Exception as e:
            log.exception("trim_cache failed")
            return {"kind": "error", "error": str(e)}
        return {"kind": "trim_cache_ok"}

    async def _handle_compute_unit(self, msg: dict) -> dict:
        """Run a single hidden-state forward through this worker's
        assigned layer range. Used by head workers chaining work
        through multi-worker placements."""
        conv_id = str(msg.get("conv_id"))
        # Taken before anything else awaits, so requests for a conversation queue in the order they came
        async with self._segment_locks.setdefault(conv_id, asyncio.Lock()):
            chunk = msg.get("chunk")
            if chunk is not None:
                expected = 0 if chunk == 0 else self._next_chunk.get(conv_id)
                if chunk != expected:  # never let a chunk into the cache out of order
                    return {"kind": "error", "error": f"prompt chunk {chunk} arrived, expected {expected}"}
                self._next_chunk[conv_id] = chunk + 1
            wire, compute_ms, error = await self._run_segment(msg, keep_last=msg.get("keep_last"))
        if error is not None:
            return {"kind": "error", "error": error}
        return {
            "kind": "compute_unit_out",
            "hidden": wire,
            # So the head can tell this Mac's time from the network's
            "compute_ms": compute_ms,
        }

    async def _run_segment(self, msg: dict, keep_last: int | None = None) -> tuple[dict | None, float, str | None]:
        """This worker's layers over a hidden state from a peer: (hidden out, compute ms, error).
        `keep_last` sends back only the last positions; the cache still holds them all."""
        model_id = msg.get("model_id") or self.model_id
        if not isinstance(model_id, str) or not model_id:
            return None, 0.0, "model_id is required"
        runtime = self._runtime_for_model(model_id)
        if runtime is None:
            return None, 0.0, "backend not selected"
        try:
            await asyncio.wait_for(
                self._ready_event_for(model_id).wait(), timeout=120.0
            )
        except asyncio.TimeoutError:
            return None, 0.0, "model not loaded yet"
        started = time.perf_counter()
        try:
            hidden_in = runtime.decode_hidden(msg["hidden"])
            hidden_out = runtime.forward_segment(msg["conv_id"], hidden_in, int(msg["layer_start"]), int(msg["layer_end"]))
            if keep_last is not None:  # 0 sends nothing back: a prompt chunk before the last
                hidden_out = hidden_out[:, hidden_out.shape[1] - int(keep_last):, :]
            wire = runtime.encode_hidden(hidden_out)  # MLX computes the layers here
        except Exception as e:
            log.exception("compute_unit failed")
            return None, 0.0, str(e)
        return wire, round((time.perf_counter() - started) * 1000, 3), None

    async def _handle_ring_unit(self, msg: dict) -> None:
        """One stop on a ring: run our layers, then hand the hidden state straight to the next peer, or, as the
        last stop, back to the head. Nothing returns along the way, so each extra peer costs one network leg
        instead of two. A failure goes straight to the head, so it never waits out a timeout."""
        hops = msg.get("hops") or []
        # Only the last stop trims: the ones before it pass every position on
        wire, compute_ms, error = await self._run_segment(msg, keep_last=None if hops else msg.get("keep_last"))
        computed = [*msg.get("compute_ms", []), compute_ms]
        try:
            if error is None and hops:
                nxt = hops[0]
                conn = await self._get_or_open_peer(str(nxt["worker_id"]), str(nxt["ticket"]))
                onward = {k: v for k, v in msg.items() if k not in ("kind", "rid")}
                await conn.notify("ring_unit", **{**onward, "hidden": wire, "layer_start": nxt["layer_start"],
                                                  "layer_end": nxt["layer_end"], "hops": hops[1:],
                                                  "compute_ms": computed})
                return
            head = await self._get_or_open_peer(str(msg["head_ticket"]), str(msg["head_ticket"]))
            await head.notify("ring_result", pass_id=msg["pass_id"], hidden=wire, compute_ms=computed, error=error)
        except Exception:
            log.exception("ring hand-off failed")

    def _handle_ring_result(self, msg: dict) -> None:
        """The hidden state coming home to the head at the end of a ring."""
        waiter = self._ring_waiters.pop(str(msg.get("pass_id")), None)
        if waiter is not None and not waiter.done():
            waiter.set_result(msg)

    async def _get_or_open_peer(self, peer_id: str, ticket: str) -> PeerConnection:
        """Cached outgoing iroh connection to a peer worker. Workers in the
        head role open one per downstream chain member (dialing the peer's
        ticket) and reuse it for every hidden-state hop across all tokens."""
        existing = self._peer_conns.get(peer_id)
        if existing is not None and not existing._closed.is_set():
            return existing
        if self._endpoint is None:
            raise RuntimeError("worker iroh endpoint not started")
        # Outgoing chain connections only RECEIVE responses, never serve
        # requests, so the handler is a no-op.
        conn = await self._endpoint.connect(ticket, _ignore_inbound)
        self._peer_conns[peer_id] = conn
        return conn

    async def _load_host_async(
        self,
        model_id: str,
        runtime: BackendRuntime,
        layer_start: int = 0,
        layer_end: int | None = None,
        needs_head: bool = True,
    ) -> None:
        """Background task: load the assigned slice into the backend runtime.

        With partial loading, this worker only materializes the assigned
        decoder layers + (if head) embed/norm/lm_head. Unowned layers
        are kept on disk (mmap'd safetensors) so peak + steady-state RAM
        stay bounded to the assignment.
        """
        slice_descr = f"layers [{layer_start}:{layer_end or '?'}]" + (
            " + head" if needs_head else ""
        )
        ui.info(f"loading {model_id} {slice_descr} on worker…")
        ready_event = self._ready_event_for(model_id)

        async def emit_progress(bytes_now: int, bytes_total: int) -> None:
            if self.conn is None:
                return
            try:
                await self.conn.notify(
                    "load_progress",
                    worker_id=self.identity,
                    model_id=model_id,
                    bytes_now=int(bytes_now),
                    bytes_total=int(bytes_total),
                )
            except Exception:
                log.debug("load_progress notify failed", exc_info=True)

        assignment = ModelAssignment(
            layer_start=layer_start,
            layer_end=layer_end,
            needs_head=needs_head,
        )
        try:
            await runtime.load(
                model_id,
                assignment,
                progress_callback=emit_progress,
            )
        except Exception as e:
            ui.error(f"model load failed on worker: {e}")
            raise
        self.backends[model_id] = runtime
        self.backend = runtime
        self.model_id = model_id
        self.n_layers = runtime.n_layers
        # Remember the assignment that produced this host, so future
        # load_units with the same (backend, model_id, range, head) become
        # no-ops.
        self._loaded_assignment = (
            runtime.capabilities.name,
            *assignment.key(model_id),
        )
        self._loaded_assignments[model_id] = self._loaded_assignment
        ready_event.set()
        self._host_ready.set()
        ui.info(
            f"worker ready · {runtime.n_layers} layers · "
            f"{runtime.active_params / 1e9:.2f}B active params"
        )
        # Tell the hub we're loaded and ready to serve turns.
        # Sniff geometry from the live model so the hub can build the
        # work-unit graph for scheduler-driven placement decisions.
        geom = runtime.geometry
        geom_payload = (
            {
                "hidden_size": geom.hidden_size,
                "vocab_size": geom.vocab_size,
                "n_layers": geom.n_layers,
                "layer_bytes_per_param": geom.layer_bytes_per_param,
                "head_bytes_per_param": geom.head_bytes_per_param,
                "layer_ram_gb": list(geom.layer_ram_gb or []),
                "embed_ram_gb": geom.embed_ram_gb,
                "lm_head_ram_gb": geom.lm_head_ram_gb,
                "head_extra_ram_gb": geom.head_extra_ram_gb,
                "tie_word_embeddings": geom.tie_word_embeddings,
                "metadata_source": geom.metadata_source,
            }
            if geom
            else None
        )

        if self.conn is not None:
            try:
                await self.conn.notify(
                    "worker_ready",
                    worker_id=self.identity,
                    model_id=model_id,
                    backend=runtime.capabilities.name,
                    n_layers=runtime.n_layers,
                    active_params=runtime.active_params,
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
            if self.host is not None
            else "loading…"
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
        hub_addr = f"hub:{self.hub_ticket[:12]}…"
        # Wait briefly for the host to load if we're also a compute worker;
        # otherwise we're a chat-only client and there's nothing to wait for.
        if self.compute:
            for _ in range(50):
                if self._host_ready.is_set():
                    break
                await asyncio.sleep(0.1)

        ui.console.print()
        ui.banner(self.requested_model_id or self.model_id, hub_addr, None)
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
        repl_task = asyncio.create_task(chat.run())  # type: ignore[attr-defined]
        closed_task = asyncio.create_task(self.wait_until_disconnected())
        done, pending = await asyncio.wait(
            {repl_task, closed_task},
            return_when=asyncio.FIRST_COMPLETED,
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
        model_id: str | None = None,
    ) -> tuple[str, dict]:
        """Send one prompt; return `(response_text, stats_dict)`.

        Streamed token chunks always accumulate into the returned string.
        If `on_token` is given, it's also invoked for each chunk as it
        arrives — used by the interactive UI to route tokens into its
        history buffer in real time.
        """
        if self.conn is None:
            raise RuntimeError("not connected to hub")
        selected_model = model_id or self.requested_model_id
        payload: dict = {"text": text, "max_tokens": max_tokens}
        if selected_model is not None:
            payload["model_id"] = selected_model
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
        if resp.get("kind") == "not_ready":
            raise PoolNotReadyError(resp.get("state", "waiting"))
        if resp.get("kind") != "turn_done":
            raise RuntimeError(resp.get("error", "turn failed"))
        return "".join(captured), resp

    async def get_pool_status(self, model_id: str | None = None) -> dict:
        """Ask the hub if the pool can serve right now.

        Returns a dict shaped:
          {"kind": "status", "ready": True}                       — serving
          {"kind": "status", "ready": False, "state": "loading"}  — workers loading
          {"kind": "status", "ready": False, "state": "waiting"}  — need more workers
        """
        if self.conn is None:
            raise RuntimeError("not connected to hub")
        selected_model = model_id or self.requested_model_id
        payload = {"model_id": selected_model} if selected_model is not None else {}
        return await self.conn.call("status", **payload)

    async def serve_model(self, model_id: str) -> dict:
        if self.conn is None:
            raise RuntimeError("not connected to hub")
        return await self.conn.call("serve_model", model_id=model_id)

    # ─────────────────────────── hub→worker handler ─────────────────────

    async def _handle(self, msg: dict) -> dict | None:
        kind = msg.get("kind")
        if kind == "load_units":
            return await self._handle_load_units(msg)
        if kind == "unload_model":
            return await self._handle_unload_model(msg)
        if kind == "delegate_turn":
            return await self._handle_delegate_turn(msg)
        if kind == "probe_peer":
            return await self._handle_probe_peer(msg)
        if kind == "make_image":
            return await self._handle_make_image(msg)
        if kind == "reset_conversation":
            conv_id = msg.get("conv_id")
            model_id = msg.get("model_id")
            if conv_id:
                if isinstance(model_id, str) and model_id:
                    runtime = self.backends.get(model_id)
                    if runtime is not None:
                        runtime.reset_conversation(conv_id)
                else:
                    for runtime in self.backends.values():
                        runtime.reset_conversation(conv_id)
                self._segment_locks.pop(conv_id, None)
                self._next_chunk.pop(conv_id, None)
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
        """Hub-driven model load. Idempotent on the EXACT assignment —
        re-issuing the same (model_id, layer_start, layer_end, needs_head)
        is a no-op. If any field differs (hub re-partitioned after a
        worker leave/join), we cancel any in-flight load and start a
        fresh one with the new assignment.
        """
        if not self.compute:
            return None
        backend_name = msg.get("backend")
        if not isinstance(backend_name, str) or not backend_name:
            log.error("refusing load_units without explicit backend")
            return None
        model_id = msg.get("model_id") or self.model_id
        layer_start = int(msg.get("layer_start", 0))
        layer_end_raw = msg.get("layer_end")
        layer_end = int(layer_end_raw) if layer_end_raw is not None else None
        needs_head = bool(msg.get("needs_head", True))

        if backend_name not in self.resource_limits.backends:
            log.error(
                "refusing load_units for backend %r; worker advertises %s",
                backend_name,
                ", ".join(sorted(self.resource_limits.backends)) or "<none>",
            )
            return None
        runtime = self.backends.get(model_id)
        if runtime is None or runtime.capabilities.name != backend_name:
            runtime = get_backend_runtime(backend_name)
            self.backends[model_id] = runtime
            self._ready_event_for(model_id).clear()
            self._loaded_assignments.pop(model_id, None)

        model_assignment = ModelAssignment(layer_start, layer_end, needs_head)
        if self.resource_limits is not None:
            prep = await runtime.preparation_requirement(
                model_id,
                model_assignment,
            )
            if prep.disk_gb > self.resource_limits.disk_gb:
                log.error(
                    "refusing model prep: needs %.1fGB disk, worker limit %.1fGB",
                    prep.disk_gb,
                    self.resource_limits.disk_gb,
                )
                return None
            if prep.ram_gb > self.resource_limits.ram_gb:
                log.error(
                    "refusing model prep: needs %.1fGB RAM, worker limit %.1fGB",
                    prep.ram_gb,
                    self.resource_limits.ram_gb,
                )
                return None
        assignment = (backend_name, model_id, layer_start, layer_end, needs_head)
        if (
            self._loaded_assignments.get(model_id) == assignment
            and getattr(runtime, "host", None) is not None
            and self._ready_event_for(model_id).is_set()
        ):
            return None
        # Different assignment than what we have. Cancel any load in
        # progress so we don't end up with two competing _load_host_async
        # tasks racing to set `self.host`.
        load_task = self._host_load_tasks.get(model_id)
        if load_task is not None and not load_task.done():
            load_task.cancel()
            try:
                await load_task
            except (asyncio.CancelledError, Exception):
                pass
        self.model_id = model_id
        self.backend = runtime
        self._ready_event_for(model_id).clear()
        self._host_ready.clear()
        self._loaded_assignments.pop(model_id, None)
        task = asyncio.create_task(
            self._load_host_async(
                model_id,
                runtime,
                layer_start,
                layer_end,
                needs_head,
            )
        )
        self._host_load_tasks[model_id] = task
        self._host_load_task = task
        return None

    async def _handle_unload_model(self, msg: dict) -> dict | None:
        """Hub asked us to drop a model we're holding so its RAM frees up
        (it's evicting an idle deployment to make room for another).
        Cancels any in-flight load, releases the runtime, and forgets all
        bookkeeping for the model. Idempotent."""
        model_id = msg.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            return {"kind": "error", "error": "model_id is required"}
        # Cancel a load still in flight so it can't resurrect the runtime.
        task = self._host_load_tasks.pop(model_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        runtime = self.backends.pop(model_id, None)
        if runtime is not None:
            try:
                await runtime.unload()
            except Exception:
                log.exception("unload failed for %s", model_id)
        self._host_ready_by_model.pop(model_id, None)
        self._loaded_assignments.pop(model_id, None)
        if self.model_id == model_id:
            self.model_id = None
        ui.info(f"unloaded {model_id} (hub reclaimed RAM)")
        return {"kind": "unload_ack", "model_id": model_id}

    async def _handle_delegate_turn(self, msg: dict) -> dict:
        """The hub delegates an entire chat turn to us.

        Runs the configured generation strategy on our backend runtime,
        streaming each generated token text fragment back to the hub via
        `delegate_token` notifications. Returns `delegate_done` with
        prefill / decode counts so the hub can attribute the FLOP-CU.
        """
        if not self.compute:
            return {"kind": "error", "error": "this client doesn't host compute"}

        # Wait for the model to finish loading. The hub may call us right
        # after HELLO; the load runs in background.
        model_id = msg.get("model_id") or self.model_id
        if not isinstance(model_id, str) or not model_id:
            return {"kind": "error", "error": "model_id is required"}
        runtime = self._runtime_for_model(model_id)
        if runtime is None:
            return {"kind": "error", "error": "backend not selected"}
        try:
            await asyncio.wait_for(
                self._ready_event_for(model_id).wait(), timeout=120.0
            )
        except asyncio.TimeoutError:
            return {"kind": "error", "error": "model not loaded yet"}

        conv_id = msg["conv_id"]
        history = msg["history"]
        template_kwargs = msg.get("template_kwargs") or None
        max_tokens = int(msg.get("max_tokens", 256))
        temperature = float(msg.get("temperature", 0.7))
        # Optional multi-worker chain spec. Empty / missing = single-worker
        # delegation (we run the whole model). Present = we're the head;
        # each chain entry tells us where to send hidden states for the
        # layers we don't own.
        chain = msg.get("chain") or []
        my_layer_end = int(msg.get("my_layer_end", runtime.n_layers))

        # One turn per conv at a time. Two interleaved decode loops on the
        # same KV cache would corrupt it.
        lock = self._turn_locks.setdefault(conv_id, asyncio.Lock())

        async def on_token(text: str) -> None:
            if self.conn is None:
                return
            try:
                await self.conn.notify(
                    "delegate_token",
                    conv_id=conv_id,
                    text=text,
                )
            except Exception:
                log.debug("delegate_token notify failed", exc_info=True)

        async def on_draft(text: str) -> None:
            """The draft model's unconfirmed proposal, for the asker to see while the pool checks it."""
            if self.conn is None:
                return
            try:
                await self.conn.notify("delegate_token", conv_id=conv_id, text=text, draft=True)
            except Exception:
                log.debug("draft notify failed", exc_info=True)

        async def on_verified(kept: int, proposed: int) -> None:
            """How a check of the draft went, so the asker can see speculation working."""
            if self.conn is None:
                return
            try:
                await self.conn.notify("delegate_token", conv_id=conv_id, draft=True, kept=kept, proposed=proposed)
            except Exception:
                log.debug("verified notify failed", exc_info=True)

        async with lock:
            try:
                forward_hidden = (
                    await self._build_chain_forwarder(
                        conv_id=conv_id,
                        model_id=model_id,
                        runtime=runtime,
                        my_layer_end=my_layer_end,
                        chain=chain,
                    )
                    if chain
                    else None
                )
                trim_remote = (
                    await self._build_chain_trimmer(conv_id=conv_id, model_id=model_id, chain=chain)
                    if chain
                    else None
                )
                generation = await self._generation_for(model_id, runtime, spans_macs=bool(chain),
                                                        speculate=bool(msg.get("speculate", True)))
                result = await generation.generate(
                    GenerationRequest(
                        runtime=runtime,
                        conv_id=conv_id,
                        history=history,
                        template_kwargs=template_kwargs,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        on_token=on_token,
                        on_draft=on_draft,
                        on_verified=on_verified,
                        forward_hidden=forward_hidden,
                        trim_remote=trim_remote,
                    )
                )
                text, stats = result.text, result.stats
                timing = getattr(forward_hidden, "timing", None)
                if timing is not None:
                    stats = {**stats, **timing.stats()}
            except Exception as e:
                log.exception("delegate_turn failed")
                return {"kind": "error", "error": str(e)}

        return {
            "kind": "delegate_done",
            "text": text,
            "active_params": runtime.active_params,
            **stats,
        }

    async def _build_chain_forwarder(
        self,
        *,
        conv_id: str,
        model_id: str,
        runtime: BackendRuntime,
        my_layer_end: int,
        chain: list,
    ):
        """Build a hidden-state forwarder for a multi-worker chain.

        We are the head: we own embed, layers [0, my_layer_end), final
        norm, lm_head, and sampling. Each `chain` entry tells us a downstream
        worker, the layer range they own, and how to reach them. Hidden
        state flows head -> chain[0] -> chain[1] -> ... -> back to us.
        """
        # Pre-open one connection per chain peer; reuse across the
        # entire turn so per-token latency is just one round-trip.
        peer_conns: list[tuple[PeerConnection, int, int]] = []
        for entry in chain:
            peer_id = str(entry["worker_id"])
            ticket = str(entry["ticket"])
            start = int(entry["layer_start"])
            end = int(entry["layer_end"])
            conn = await self._get_or_open_peer(peer_id, ticket)
            peer_conns.append((conn, start, end))

        if os.environ.get("DOTS_CHAIN", "star").strip() == "ring" and self._peer_ticket is not None:
            return self._ring_forwarder(conv_id=conv_id, model_id=model_id, runtime=runtime,
                                        my_layer_end=my_layer_end, chain=chain, first=peer_conns[0][0])

        timing = ChainTiming(peers=len(peer_conns))
        chunk_tokens = int(os.environ.get("DOTS_PREFILL_CHUNK", "1024"))

        async def through_peers(wire, chunk: int | None, keep_last: int | None):
            """One hidden state through every peer in turn; returns what the last one sends back."""
            for i, (conn, start, end) in enumerate(peer_conns):
                last = i == len(peer_conns) - 1
                resp = await conn.call("compute_unit", model_id=model_id, conv_id=conv_id, layer_start=start,
                                       layer_end=end, hidden=wire, chunk=chunk, keep_last=keep_last if last else None)
                if resp.get("kind") != "compute_unit_out":
                    raise RuntimeError(f"compute_unit failed: {resp.get('error', resp)}")
                wire = resp["hidden"]
            return wire

        async def prefill_in_chunks(hidden, keep_last: int):
            """A long prompt in chunks: while the peers work on one chunk, this Mac computes and sends the
            next, so compute and network overlap instead of taking turns. Only the last chunk's last rows
            come back; the earlier ones return nothing."""
            n = hidden.shape[1]
            starts = list(range(0, n, chunk_tokens))
            in_flight = []
            for c, start in enumerate(starts):
                piece = runtime.forward_segment(conv_id, hidden[:, start:start + chunk_tokens, :], 0, my_layer_end)
                last = c == len(starts) - 1
                wire = runtime.encode_hidden(piece)
                in_flight.append(asyncio.create_task(through_peers(wire, c, keep_last if last else 0)))
                await asyncio.sleep(0)  # let this chunk's send go out before computing the next
            results = await asyncio.gather(*in_flight)
            return runtime.decode_hidden(results[-1])

        async def forward_through_chain(hidden, keep_last: int | None = None):
            if keep_last and chunk_tokens > 0 and hidden.shape[1] > chunk_tokens:
                return await prefill_in_chunks(hidden, keep_last)
            # Our local segment first.
            started = time.perf_counter()
            hidden = runtime.forward_segment(conv_id, hidden, 0, my_layer_end)
            # Then each downstream chain segment.
            for i, (conn, start, end) in enumerate(peer_conns):
                wire = runtime.encode_hidden(hidden)  # MLX computes our layers here
                sent = time.perf_counter()
                if i == 0:
                    timing.here_ms += (sent - started) * 1000
                resp = await conn.call(
                    "compute_unit",
                    model_id=model_id,
                    conv_id=conv_id,
                    layer_start=start,
                    layer_end=end,
                    hidden=wire,
                    # The last Mac sends back only what the head needs (one row for a prompt)
                    keep_last=keep_last if i == len(peer_conns) - 1 else None,
                )
                if resp.get("kind") != "compute_unit_out":
                    raise RuntimeError(
                        f"compute_unit failed: {resp.get('error', resp)}"
                    )
                timing.round_trip_ms[i] += (time.perf_counter() - sent) * 1000
                timing.peer_compute_ms[i] += float(resp.get("compute_ms") or 0.0)
                hidden = runtime.decode_hidden(resp["hidden"])
            timing.passes += 1
            return hidden

        forward_through_chain.timing = timing
        return forward_through_chain

    def _ring_forwarder(self, *, conv_id: str, model_id: str, runtime: BackendRuntime, my_layer_end: int,
                        chain: list, first: PeerConnection):
        """Like the star forwarder, but the hidden state goes peer to peer and comes home once.

        With n peers that is n + 1 network legs a pass instead of 2n. The head waits on a future that the
        last peer's `ring_result` resolves; a peer that fails reports straight here.
        """
        hops = [{"worker_id": str(e["worker_id"]), "ticket": str(e["ticket"]),
                 "layer_start": int(e["layer_start"]), "layer_end": int(e["layer_end"])} for e in chain]
        timeout = float(os.environ.get("DOTS_RING_TIMEOUT_S", "120"))
        timing = ChainTiming(peers=1, ring=True)

        async def forward_round_ring(hidden, keep_last: int | None = None):
            started = time.perf_counter()
            hidden = runtime.forward_segment(conv_id, hidden, 0, my_layer_end)
            wire = runtime.encode_hidden(hidden)  # MLX computes our layers here
            sent = time.perf_counter()
            timing.here_ms += (sent - started) * 1000
            pass_id = uuid.uuid4().hex
            home = asyncio.get_running_loop().create_future()
            self._ring_waiters[pass_id] = home
            try:
                await first.notify("ring_unit", model_id=model_id, conv_id=conv_id,
                                   layer_start=hops[0]["layer_start"], layer_end=hops[0]["layer_end"],
                                   hidden=wire, hops=hops[1:], head_ticket=self._peer_ticket,
                                   pass_id=pass_id, compute_ms=[], keep_last=keep_last)
                result = await asyncio.wait_for(home, timeout=timeout)
            finally:
                self._ring_waiters.pop(pass_id, None)
            if result.get("error"):
                raise RuntimeError(f"ring pass failed: {result['error']}")
            timing.round_trip_ms[0] += (time.perf_counter() - sent) * 1000
            timing.peer_compute_ms[0] += sum(result.get("compute_ms") or [])
            timing.passes += 1
            return runtime.decode_hidden(result["hidden"])

        forward_round_ring.timing = timing
        return forward_round_ring

    async def _build_chain_trimmer(self, *, conv_id: str, model_id: str, chain: list):
        """Drops rejected draft tokens from every chain member's cache, reusing the turn's connections."""
        conns = [
            await self._get_or_open_peer(str(entry["worker_id"]), str(entry["ticket"]))
            for entry in chain
        ]

        async def trim_through_chain(n: int) -> None:
            for conn in conns:
                resp = await conn.call("trim_cache", model_id=model_id, conv_id=conv_id, n=n)
                if resp.get("kind") != "trim_cache_ok":
                    raise RuntimeError(f"trim_cache failed: {resp.get('error', resp)}")

        return trim_through_chain

    async def _generation_for(self, model_id: str, runtime: BackendRuntime, *, spans_macs: bool,
                              speculate: bool = True):
        """How this turn writes: guessing ahead where it pays, one token at a time otherwise.

        Guesses looked up in the conversation are free, so every turn uses them (scripts/bench_decode.py:
        answers that copy or edit what they were given came 2.55x faster on one Mac, others the same).
        `DOTS_DRAFT_MODEL` adds the model's own draft head, on every turn, or a small draft model, on turns
        that cross the network, where it pays (`draft_for`).
        """
        lookup = os.environ.get("DOTS_PROMPT_LOOKUP", "1") != "0"
        if not speculate or not runtime.supports_speculation:
            return self.generation
        draft = None
        draft_id = os.environ.get("DOTS_DRAFT_MODEL", "").strip()
        if draft_id and draft_id != model_id:
            draft = draft_for(await self._load_draft(draft_id, model_id, runtime), runtime, spans_macs=spans_macs)
        if draft is None and not lookup:
            return self.generation
        guesses = int(os.environ.get("DOTS_DRAFT_TOKENS", draft.most_guesses if draft is not None else 4))
        return SpeculativeGeneration(draft, draft_tokens=guesses,
                                     adaptive=os.environ.get("DOTS_DRAFT_ADAPTIVE", "1") != "0", lookup=lookup)

    async def _load_draft(self, draft_id: str, model_id: str, runtime: BackendRuntime):
        """The draft, loaded on this Mac the first time this model needs it. None if it won't load."""
        key = (draft_id, model_id)
        if key not in self._drafts:
            try:
                self._drafts[key] = await asyncio.to_thread(load_draft, draft_id, runtime)
            except Exception:
                log.exception("draft %s failed to load for %s; guessing ahead without it", draft_id, model_id)
                self._drafts[key] = None
        return self._drafts[key]

    async def _handle_make_image(self, msg: dict) -> dict:
        """Draws one picture, whole, with the pool's model and the runner this Mac has for it, and sends it back as
        base64 PNG. The job names the model; the command is always the runner's own."""
        runner = image_runners.for_original(msg.get("original"), self.runners)
        if runner is None:
            return {"kind": "error", "error": "this Mac can't draw this pool's model"}
        try:
            tool = runner.tool(str(msg.get("model") or ""))
            image = msg.get("image")
            job = ImageJob(prompt=str(msg.get("prompt", "")), width=msg.get("width"), height=msg.get("height"),
                           steps=msg.get("steps"), seed=msg.get("seed"),
                           image=base64.b64decode(image) if image else None, strength=msg.get("strength"),
                           keep_unchanged=bool(msg.get("keep_unchanged", True)))
        except (ValueError, TypeError, binascii.Error) as e:
            return {"kind": "error", "error": str(e)}
        job_id = msg.get("job_id")

        async def on_step(step: int, total: int, preview: bytes) -> None:
            if job_id and self.conn is not None:
                await self.conn.notify("image_step", job_id=job_id, step=step, of=total,
                                       preview=base64.b64encode(preview).decode())

        async with self._drawing:
            try:
                png, ms = await tool.draw(job, on_step if job_id else None)
            except RuntimeError as e:
                log.warning("image job failed: %s", e)
                return {"kind": "error", "error": str(e)}
        return {"kind": "image_ok", "png": base64.b64encode(png).decode(), "model": tool.model, "ms": ms}

    async def _handle_probe_peer(self, msg: dict) -> dict:
        """Hub-triggered probe: open (or reuse) a connection to the target
        peer, time N pings, return the RTT statistics to the hub."""
        import time as _time

        target_id = msg.get("target_id")
        target_ticket = msg.get("target_ticket")
        samples = int(msg.get("samples", 3))
        if not target_id or not target_ticket:
            return {
                "kind": "probe_result",
                "target_id": target_id,
                "error": "missing target id/ticket",
            }
        rtts: list[float] = []
        try:
            # Reuse the cached chain connection to this peer if one exists
            # (and establish it via iroh otherwise) — probing and chaining
            # share the one direct link.
            conn = await self._get_or_open_peer(str(target_id), target_ticket)
            for _ in range(samples):
                t0 = _time.perf_counter_ns()
                try:
                    resp = await asyncio.wait_for(
                        conn.call("peer_ping", ts_ns=t0),
                        timeout=2.0,
                    )
                except asyncio.TimeoutError:
                    continue
                if resp.get("kind") != "peer_pong":
                    continue
                rtt_ms = (_time.perf_counter_ns() - t0) / 1e6
                rtts.append(rtt_ms)
        except Exception as e:
            return {"kind": "probe_result", "target_id": target_id, "error": str(e)}
        if not rtts:
            return {
                "kind": "probe_result",
                "target_id": target_id,
                "error": "no successful pings",
            }
        rtts.sort()
        return {
            "kind": "probe_result",
            "target_id": target_id,
            "rtt_p50_ms": rtts[len(rtts) // 2],
            "rtt_p99_ms": rtts[-1],
            "samples": len(rtts),
        }

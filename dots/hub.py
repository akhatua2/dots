"""Hub: pure orchestrator. The pool is the set of connected workers.

The hub itself does no model compute. It:

  • Accepts worker TCP connections. On HELLO with a ResourceDecl, the
    worker is added to the pool and asynchronously starts loading the
    full model. On `worker_ready` notification it becomes eligible to
    serve turns.
  • Maintains one Conversation per chat-client participant.
  • On a chat-client PROMPT, picks a compute worker via the scheduler,
    sends `delegate_turn(conv_id, history, max_tokens, temperature)`, and
    forwards each `delegate_token` notification back to the chat client
    as a `token` notification. When the worker replies `delegate_done`,
    the turn is finished and the ledger is updated.

Empty pool → hub returns errors until a worker joins.

Architectural pieces:
  • dots.scheduler    — placement algorithm (workers + latency → unit assignment)
  • dots.sharding     — strategies (PP / TP / EP); v1 ships PP
  • dots.compute_plane — abstraction the model talks to (wraps scheduler + sharding)
  • dots.pool         — hub-side state (resources + latency matrix)
  • dots.work_units   — per-token DAG (embed, layers, lm_head, sample)
  • dots.inference    — the actual inference loop, owned by `FullModelHost`
                        on each worker
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from . import ui
from .ledger import Ledger
from .peer import PeerConnection
from .pool import Pool, resource_decl_from_hello
from .scheduler import Placement, WorkUnit, schedule
from .util import dots_home
from .work_units import ModelGeometry, units_for_geometry

log = logging.getLogger("dots.hub")

HELLO_TIMEOUT_SEC = 30.0
DEFAULT_MAX_NEW_TOKENS = 256
DEFAULT_TEMPERATURE = 0.7


@dataclass
class Conversation:
    id: str
    history: list[dict] = field(default_factory=list)
    token_count: int = 0


@dataclass
class Participant:
    """A connected worker (or chat-only client) and its hub-side state."""
    id: str                                    # worker's stable UUID
    addr: str
    conn: PeerConnection
    compute: bool = True                       # contributes layers to the pool
    layer_range: tuple[int, int] | None = None
    ready: bool = False
    # The human-owner account this worker contributes to. CU and the
    # ledger get attributed against this, not against the per-process
    # `id` above. Multiple Participants can share an account.
    account_id: str | None = None
    account_alias: str | None = None
    # Where this worker accepts incoming peer connections (other workers
    # talk to it on this port for probes + the multi-worker chain). The
    # host part comes from the TCP socket peername; the port comes from
    # HELLO's `peer_listen_port`. None means the worker didn't open a
    # peer server (chat-only clients).
    peer_listen_port: int | None = None
    conv: Conversation = field(default_factory=lambda: Conversation(id=str(uuid.uuid4())))

    @property
    def peer_host(self) -> str:
        """Host portion of `addr` for use as a probe target."""
        return self.addr.split(":")[0]

    @property
    def short_id(self) -> str:
        return self.id[:8] if self.id else "?"

    @property
    def display_owner(self) -> str:
        """Best-effort short label for who owns this participant. Used in
        log lines. Prefers an explicit alias, falls back to a truncated
        account_id, then 'anon' if the participant predates accounts."""
        if self.account_alias:
            return self.account_alias
        if self.account_id:
            short = (self.account_id[4:] if self.account_id.startswith("acc-")
                     else self.account_id)
            return short[:8]
        return "anon"


class Hub:
    def __init__(
        self,
        port: int,
        model_id: str,
        host: str = "0.0.0.0",
        http_port: int | None = None,
        ledger_path=None,
    ):
        self.host = host
        self.port = port
        self.http_port = http_port
        self.model_id = model_id

        # The hub does not load a model in the delegation architecture.
        # `n_layers` and `active_params` come in from workers via the
        # `worker_ready` notification and are used by the ledger for
        # FLOP-CU attribution.
        self.n_layers = 0
        self.active_params = 0

        self.participants: dict[str, Participant] = {}
        self._pool_lock = asyncio.Lock()
        self._pool_ready = asyncio.Event()  # at least one ready participant
        # Set after the model is loaded AND the server is listening — useful
        # for callers (tests, embeddings) that want a deterministic ready signal.
        self.started = asyncio.Event()
        self._topology_gen = 0
        # Closed credit-unit economy. Persists per-account totals + a
        # per-turn audit log. Tests pass an explicit path so they don't
        # touch the developer's real ~/.dots/credits.json.
        self.ledger = Ledger(ledger_path or (dots_home() / "credits.json"))
        # Monotonic turn counter; used as the per-conv turn_idx in the
        # ledger. Conversations carry their own count too; we rely on
        # this for the ledger entry key.
        self._turn_counter: dict[str, int] = {}
        # Hub-side pool view: resource declarations + pairwise latency. Fed
        # by HELLO (resources) and periodic hub→worker pings (used as a
        # proxy for inter-worker RTT until true worker-to-worker probes
        # land). Snapshots fed into the scheduler when planning a query.
        self.pool_view = Pool()
        # Cached model geometry for scheduler unit-graph building. Populated
        # once after model load.
        self._geometry: ModelGeometry | None = None
        # conv_id → async callback for streaming tokens back to the chat
        # client during a delegate_turn. Populated in `run_turn` and
        # consulted in `_on_delegate_token`.
        self._token_routers: dict[str, "asyncio.Future | object"] = {}
        # Locked-in unit assignment from `_assign_units_to_workers`. Used
        # both for `load_units` broadcasts and for building the chain
        # spec at delegation time so the two views can't drift.
        self._current_placement: list[tuple[str, int, int, bool]] = []
        # Per-worker (bytes_now, bytes_total) snapshots. Workers emit
        # periodic `load_progress` notifications during load_partial; we
        # aggregate the slowest worker's pair into the chat client's
        # status response (bar + raw "X / Y GB" so users can tell
        # something is happening on a slow connection even when the
        # rounded percentage is still 0).
        self._load_progress: dict[str, tuple[int, int]] = {}

    # ───────────────────────────── lifecycle ─────────────────────────────

    async def start(self) -> None:
        # The hub doesn't load model weights, but it does read the
        # model's config.json upfront so it can plan placements before
        # any worker has even started loading. This is the cheap part
        # of "model awareness" — kilobytes off disk.
        await self._prime_geometry_from_config()
        ui.notice(
            f"hub starting · model = {self.model_id}"
            + (f" · {self.n_layers} layers" if self.n_layers else "")
        )
        server = await asyncio.start_server(self._on_connect, self.host, self.port)
        addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
        ui.notice(f"hub listening on {addrs} — waiting for workers to join")
        self.started.set()

        # OpenAI-compatible HTTP API alongside the worker TCP server. On
        # by default; users can pass --http-port 0 to disable. Lives only
        # for as long as the hub does; cancellation tears it down.
        http_task: asyncio.Task | None = None
        if self.http_port:
            from .api import HttpApi
            api = HttpApi(self, host=self.host, port=self.http_port)
            http_task = asyncio.create_task(api.serve_forever())

        try:
            await server.serve_forever()
        except asyncio.CancelledError:
            server.close()
            if http_task is not None:
                http_task.cancel()
                try:
                    await http_task
                except (asyncio.CancelledError, Exception):
                    pass
            raise

    async def _prime_geometry_from_config(self) -> None:
        """Read config.json off disk (downloading just the JSON metadata
        if needed) so the scheduler can plan placements before workers
        load. The hub never downloads the safetensors weights — that's
        the workers' job. Logs clearly when the model can't be resolved.
        """
        try:
            from mlx_lm.utils import _download, load_config

            def _config_only(repo: str):
                # Pull the config files (a few KB), NOT *.safetensors.
                # Without restricting allow_patterns, _download grabs
                # the full ~18 GB of weights — wrong for the hub since
                # workers handle weight loading themselves.
                return _download(repo, allow_patterns=[
                    "config.json", "generation_config.json",
                    "tokenizer_config.json",
                ])

            model_path = await asyncio.to_thread(_config_only, self.model_id)
            cfg = await asyncio.to_thread(load_config, model_path)
        except Exception as e:
            ui.error(
                f"could not load model config for '{self.model_id}': {e}. "
                f"check the model id — common typos include 30B (use 32B) "
                f"or omitted '-4bit' / '-Instruct' suffixes."
            )
            return
        n_layers = int(cfg.get("num_hidden_layers")
                       or cfg.get("text_config", {}).get("num_hidden_layers")
                       or 0)
        hidden = int(cfg.get("hidden_size")
                     or cfg.get("text_config", {}).get("hidden_size")
                     or 0)
        vocab = int(cfg.get("vocab_size")
                    or cfg.get("text_config", {}).get("vocab_size")
                    or 0)
        if not (n_layers and hidden and vocab):
            ui.error(
                f"model '{self.model_id}' has an unexpected config shape "
                f"(missing num_hidden_layers / hidden_size / vocab_size). "
                f"this hub can't plan placements for it."
            )
            return
        self.n_layers = n_layers
        self._geometry = ModelGeometry(
            n_layers=n_layers, hidden_size=hidden, vocab_size=vocab,
        )

    def _assign_units_to_workers(self) -> list[tuple[str, int, int, bool]]:
        """RAM-aware placement.

        If the model fits on any single compute worker, place it entirely
        on the one with the most free RAM — that's the optimal answer
        (zero network, max TPS). Otherwise split decoder layers across
        workers proportional to their free RAM, biggest-RAM worker as
        the head (carries the embed + lm_head + sample overhead).

        Returns `(worker_id, layer_start, layer_end, needs_head)` for
        every worker that should load something. Workers not in the
        return list stay idle (e.g. when one beefy worker hosts the
        whole model, smaller workers don't bother loading).

        Placement is locked at first-stable: workers joining later don't
        force a re-placement (which would mean a multi-GB re-download
        per already-loaded peer). New workers stay idle until the locked
        members all leave.
        """
        if not self.n_layers or self._geometry is None:
            # Geometry priming failed (bad model id, no network, …) —
            # the operator already saw an error from _prime_geometry,
            # so don't spam every pool change with the same notice.
            return []
        compute_workers = sorted(
            (p for p in self.participants.values() if p.compute),
            key=lambda p: p.id,
        )
        if not compute_workers:
            return []

        model_ram_gb = self._estimate_model_ram_gb()
        head_overhead_gb = self._estimate_head_overhead_gb()
        layer_ram_gb = self._estimate_layer_ram_gb()
        # Small margin so we never assign right up to a worker's edge.
        margin_gb = 1.0

        def free_ram(p: "Participant") -> float:
            decl = self.pool_view.resources.get(p.id)
            return decl.ram_gb_free if decl is not None else 0.0

        # Default: every worker that can fit the model holds a full
        # copy. This gives each querier self-utilization — their own
        # machine answers their queries with zero network. Workers that
        # can't fit the full model stay idle and can only chat (their
        # queries route to whichever loaded worker the scheduler picks).
        fitters = [
            p for p in compute_workers
            if free_ram(p) >= model_ram_gb + margin_gb
        ]
        if fitters:
            # Don't cache — additional workers joining later should also
            # load (they fit). New workers get the assignment on the
            # next pool change.
            out = [(p.id, 0, self.n_layers, True) for p in fitters]
            self._current_placement = out
            return out

        # No single worker fits — partition every pool change. We used
        # to lock the first partition to "avoid restarting downloads on
        # each topology change", but partial_loader uses the HF disk
        # cache (which the OS keeps across re-loads), so re-running
        # load_partial with a different layer range only re-filters
        # the tensors that go into RAM. Cheap enough to do whenever a
        # new worker joins and might enable a better split.

        # Partition via the scheduler. The unit graph is one WorkUnit
        # per decoder layer; layer 0 carries the head overhead so the
        # scheduler will only place it on a worker that can hold
        # embed + lm_head + working memory in addition to its layer share.
        # That's how we constrain "head must fit at segment 0" without
        # the scheduler needing to know about head modules.
        per_layer_flops = float(
            2 * 16 * (self._geometry.hidden_size ** 2)
        )
        units = []
        for i in range(self.n_layers):
            unit_ram = layer_ram_gb + (head_overhead_gb if i == 0 else 0.0)
            units.append(WorkUnit(
                unit_id=f"block_{i}",
                ram_required_gb=unit_ram + (margin_gb if i == 0 else 0),
                flops_per_token=per_layer_flops,
            ))

        workers_snapshot, latency = self.pool_view.snapshot()
        ready_ids = {p.id for p in compute_workers}
        workers_snapshot = [
            w for w in workers_snapshot if w.worker_id in ready_ids
        ]
        if not workers_snapshot:
            self._current_placement = []
            return []

        placement = schedule(
            units, workers_snapshot, latency,
            querier_account=None,
            mode="cheap",
            max_segments=len(workers_snapshot),
        )
        if placement is None or not placement.segments:
            pool_ram = sum(free_ram(p) for p in compute_workers)
            short_by = max(0.0, model_ram_gb - pool_ram)
            ui.notice(
                f"need ~{short_by:.0f} GB more pool RAM to load this model "
                f"(have {pool_ram:.1f} GB, model needs ~{model_ram_gb:.1f} GB)"
            )
            self._current_placement = []
            return []

        out: list[tuple[str, int, int, bool]] = []
        cursor = 0
        for i, (worker_id, unit_ids) in enumerate(placement.segments):
            count = len(unit_ids)
            out.append((worker_id, cursor, cursor + count, i == 0))
            cursor += count
        log.info(
            "partition placement: %s",
            " → ".join(f"{wid[:8]}[{s}:{e}]" for wid, s, e, _ in out),
        )
        self._current_placement = out
        return out

    def _estimate_layer_ram_gb(self) -> float:
        """Per-decoder-layer weight footprint at 4-bit (the common dots
        target). 16·H² params × 0.5 B/param ≈ 8·H²; convert to GB."""
        if self._geometry is None:
            return 0.5  # safe-ish default
        return 16 * (self._geometry.hidden_size ** 2) * 0.5 / 1e9

    def _estimate_head_overhead_gb(self) -> float:
        """Embed + lm_head storage on the head worker (bf16, unquantized)."""
        if self._geometry is None:
            return 1.0
        head_params = self._geometry.vocab_size * self._geometry.hidden_size
        # Roughly: embed (bf16) + lm_head (bf16) + working / KV margin.
        return head_params * 2 * 2 / 1e9 + 0.5

    def _estimate_model_ram_gb(self) -> float:
        return (
            self.n_layers * self._estimate_layer_ram_gb()
            + self._estimate_head_overhead_gb()
        )

    async def _broadcast_load_units(self, assignments) -> None:
        """Tell each worker which layer range to load + whether it's
        the head (also owns embed/norm/lm_head/sample).

        Idempotent: a worker that already received this same assignment
        is skipped. Otherwise every pool-change would re-trigger
        load_units on in-progress workers and force them to restart
        their multi-GB downloads from scratch.
        """
        instructed = 0
        for worker_id, layer_start, layer_end, needs_head in assignments:
            p = self.participants.get(worker_id)
            if p is None or not p.compute:
                continue
            new_range = (int(layer_start), int(layer_end))
            # If this worker is already loading (or has loaded) exactly
            # this slice, leave them alone. Re-broadcasting would
            # restart the download.
            if p.layer_range == new_range:
                continue
            try:
                await p.conn.notify(
                    "load_units",
                    model_id=self.model_id,
                    layer_start=new_range[0],
                    layer_end=new_range[1],
                    needs_head=bool(needs_head),
                )
                p.layer_range = new_range
                p.ready = False  # range changed → must re-validate via worker_ready
                head_tag = " + head" if needs_head else ""
                ui.notice(
                    f"worker·{p.short_id} ({p.display_owner}) "
                    f"loading layers [{layer_start}:{layer_end}]{head_tag}…"
                )
                instructed += 1
            except Exception:
                log.debug("load_units notify failed for %s", worker_id, exc_info=True)
        if instructed > 1:
            ui.notice(
                f"(first-time downloads can take a few minutes — "
                f"pool will be ready when each worker reports back)"
            )

    # ─────────────────────────── connection lifecycle ────────────────────

    async def _on_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer_addr = writer.get_extra_info("peername")
        addr_str = f"{peer_addr[0]}:{peer_addr[1]}"

        # worker_id is resolved from the HELLO payload below. Until then
        # we can't route worker-initiated messages (prompt / reset), so the
        # handler closes over a small holder that gets filled in after HELLO.
        wid_holder: dict[str, str | None] = {"id": None}
        hello_fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()

        async def handler(msg: dict) -> dict | None:
            kind = msg.get("kind")
            if kind == "hello":
                if not hello_fut.done():
                    hello_fut.set_result(msg)
                return {"kind": "hello_ack", "n_layers": self.n_layers,
                        "model_id": self.model_id}
            wid = wid_holder["id"]
            if kind == "prompt" and wid is not None:
                return await self._on_prompt(wid, msg)
            if kind == "reset" and wid is not None:
                return await self._on_reset(wid)
            if kind == "worker_ready" and wid is not None:
                return self._on_worker_ready(wid, msg)
            if kind == "delegate_token" and wid is not None:
                return await self._on_delegate_token(wid, msg)
            if kind == "load_progress" and wid is not None:
                bytes_now = int(msg.get("bytes_now") or 0)
                bytes_total = int(msg.get("bytes_total") or 0)
                if bytes_total > 0:
                    # Clamp so workers reporting bytes_now > bytes_total
                    # (rare, but possible if HF API under-estimated) don't
                    # drag the bar past 100%.
                    self._load_progress[wid] = (
                        min(bytes_now, bytes_total), bytes_total,
                    )
                return None
            if kind == "status":
                return self._status_response_for(wid)
            return {"kind": "error",
                    "error": f"unexpected worker-initiated kind: {kind}"}

        conn = PeerConnection(reader, writer, addr_str, handler)
        log.info("connection from %s", addr_str)

        try:
            hello_msg = await asyncio.wait_for(hello_fut, timeout=HELLO_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            log.warning("connection from %s: HELLO timeout, dropping", addr_str)
            await conn.close()
            return

        worker_id = hello_msg.get("worker_id")
        if not isinstance(worker_id, str) or not worker_id:
            log.warning("HELLO without worker_id from %s; dropping", addr_str)
            await conn.close()
            return
        wid_holder["id"] = worker_id
        compute = bool(hello_msg.get("compute", True))
        account_id = hello_msg.get("account_id")
        account_id = account_id if isinstance(account_id, str) and account_id else None
        account_alias = hello_msg.get("account_alias")
        account_alias = (account_alias if isinstance(account_alias, str)
                         and account_alias else None)

        resource_decl = resource_decl_from_hello(hello_msg, worker_id=worker_id)
        free_ram = resource_decl.ram_gb_free if resource_decl is not None else 0.0

        # If a worker with this identity is already in the pool (process
        # restart whose old connection hasn't been read-detected yet),
        # close the stale one and replace it.
        async with self._pool_lock:
            stale = self.participants.get(worker_id)
            if stale is not None and stale.conn is not conn:
                ui.notice(f"w[{worker_id[:8]}] reconnected; replacing stale connection")
                try:
                    await stale.conn.close()
                except Exception:
                    pass
            peer_port_raw = hello_msg.get("peer_listen_port")
            peer_port = (int(peer_port_raw)
                         if isinstance(peer_port_raw, int) and peer_port_raw > 0
                         else None)
            participant = Participant(
                id=worker_id, addr=addr_str,
                compute=compute, conn=conn,
                account_id=account_id, account_alias=account_alias,
                peer_listen_port=peer_port,
            )
            self.participants[worker_id] = participant
            if resource_decl is not None:
                self.pool_view.add_or_update(worker_id, resource_decl)
        role = "worker" if compute else "client"
        ui.notice(f"{role}·{participant.short_id} ({participant.display_owner}) "
                  f"joined ({addr_str}, free_ram={free_ram:.1f}GB)")
        await self._on_pool_changed()
        try:
            await conn.notify("welcome", conv_id=participant.conv.id)
        except Exception:
            pass

        try:
            await conn._closed.wait()
        finally:
            async with self._pool_lock:
                # Only remove if we're still the entry — guards the
                # takeover-on-reconnect case.
                if self.participants.get(worker_id) is participant:
                    self.participants.pop(worker_id, None)
                    self.pool_view.remove(worker_id)
                # If this worker was part of the locked placement, clear
                # the cache so the next pool-changed builds a fresh one.
                if any(seg[0] == worker_id for seg in self._current_placement):
                    self._current_placement = []
            left_role = "worker" if participant.compute else "client"
            ui.notice(f"{left_role}·{participant.short_id} "
                      f"({participant.display_owner}) left")
            await self._on_pool_changed()

    # ─────────────────────────── re-plan on change ──────────────────────

    async def _on_pool_changed(self) -> None:
        """Topology change handler.

        Workers don't auto-load anymore. After every join/leave we
        recompute the placement and tell any *unready* workers what
        layer slice they should load. Already-loaded workers stay put —
        re-placing them would force a multi-GB reload, and the chain
        protocol routes around mis-sized assignments anyway.
        """
        assignments: list[tuple[str, int, int, bool]] = []
        async with self._pool_lock:
            self._topology_gen += 1
            workers = [p for p in self.participants.values() if p.compute]
            self._pool_ready.clear()
            if not workers:
                ui.notice("no compute workers — waiting")
                return
            assignments = self._assign_units_to_workers()
            if any(p.ready for p in workers):
                self._pool_ready.set()

        if assignments:
            await self._broadcast_load_units(assignments)

    # ────────────────────────────── one turn ────────────────────────────

    async def _on_prompt(self, worker_id: str, msg: dict) -> dict:
        """Wire-protocol entry point for chat-client PROMPT messages.
        Owns conversation-history bookkeeping (append user msg / append
        assistant response / rollback on failure) and delegates the
        actual turn to `run_turn`."""
        async with self._pool_lock:
            participant = self.participants.get(worker_id)
        if participant is None:
            return {"kind": "error", "error": "worker no longer in pool"}
        if not self._pool_ready.is_set():
            return self._not_ready_status_response()

        conv = participant.conv
        conv.history.append({"role": "user", "content": msg["text"]})

        async def emit(text: str) -> None:
            await participant.conn.notify("token", text=text)

        def alive() -> bool:
            return participant.id in self.participants

        try:
            response_text, stats = await self.run_turn(
                conv,
                max_new_tokens=int(msg.get("max_tokens", DEFAULT_MAX_NEW_TOKENS)),
                temperature=float(msg.get("temperature", DEFAULT_TEMPERATURE)),
                on_token=emit,
                is_alive=alive,
                querier_account=participant.account_id,
            )
        except ConnectionError as e:
            if conv.history and conv.history[-1].get("role") == "user":
                conv.history.pop()
            return {"kind": "error", "error": f"worker dropped mid-turn: {e}"}
        except Exception as e:
            log.exception("turn failed for %s", worker_id)
            if conv.history and conv.history[-1].get("role") == "user":
                conv.history.pop()
            return {"kind": "error", "error": str(e)}

        conv.history.append({"role": "assistant", "content": response_text})
        # Closed-economy attribution. After a successful turn, credit each
        # compute worker by (layers_owned × total_tokens) and debit the
        # querier the same total. Quiet failure if anything goes wrong —
        # the user-visible response should not be blocked by ledger I/O.
        try:
            self._attribute_turn(conv=conv, participant=participant, stats=stats)
        except Exception:
            log.exception("ledger attribution failed for %s", worker_id)
        return {"kind": "turn_done", **stats}

    def _attribute_turn(
        self, conv: "Conversation", participant: "Participant", stats: dict,
    ) -> None:
        """Record one successful turn in the ledger. Computes the plan
        snapshot from currently-ready compute workers (post-turn state),
        credits each by its layer share, and debits the querier.

        Skips silently if there's no model context yet (test fakes,
        active_params unavailable) or the querier has no account.
        """
        if self.active_params <= 0:
            return  # no real model loaded (tests with fakes) → no FLOP unit
        if not participant.account_id:
            log.debug("skipping ledger entry — chat client has no account_id")
            return

        plan: list[tuple[str, str | None, int]] = []
        total_layers_assigned = 0
        for p in self.participants.values():
            if not (p.compute and p.ready and p.layer_range and p.account_id):
                continue
            layers_owned = p.layer_range[1] - p.layer_range[0]
            plan.append((p.account_id, p.account_alias, layers_owned))
            total_layers_assigned += layers_owned
        if not plan or total_layers_assigned == 0:
            return  # nothing to attribute (e.g. failure or no compute)

        prefill_tokens = int(stats.get("prefill_tokens", 0))
        decode_tokens = int(stats.get("decode_tokens", 0))
        if prefill_tokens + decode_tokens == 0:
            return  # nothing was generated; charge nothing

        self._turn_counter[conv.id] = self._turn_counter.get(conv.id, 0) + 1
        self.ledger.record_turn(
            conv_id=conv.id,
            turn_idx=self._turn_counter[conv.id],
            querier_account=participant.account_id,
            querier_alias=participant.account_alias,
            plan=plan,
            prefill_tokens=prefill_tokens,
            decode_tokens=decode_tokens,
            total_layers=total_layers_assigned,
            model_id=self.model_id,
            active_params=self.active_params,
        )

    async def _on_reset(self, worker_id: str) -> dict:
        async with self._pool_lock:
            participant = self.participants.get(worker_id)
        if participant is None:
            return {"kind": "error", "error": "worker no longer in pool"}
        await self.reset_conversation_on_workers(participant.conv.id)
        participant.conv.history.clear()
        participant.conv.token_count = 0
        return {"kind": "ok"}

    def _status_response_for(self, asker_wid: str | None) -> dict:
        """Build the status response a chat client sees during pre-flight.

        Returns `{ready: True}` when the pool can serve. Otherwise
        `{ready: False, state: "loading"|"waiting", ?progress, ?bytes_now,
        ?bytes_total}`.

        Per-machine routing: chat and its auto-spawned companion worker
        share `account_id` (both load `~/.dots/account`), so the asker's
        progress is routed to the worker on the same account. This stops
        a far-away peer's slow download from dragging every user's bar.
        If the asker's local worker has finished but the pool isn't
        ready (remote workers still loading), we drop the bar so it
        doesn't freeze at 100% — the friendly "Loading the model"
        spinner stays. Chat-only clients with no matching local worker
        fall back to the MOST-progressed reporter so the bar moves
        visibly instead of being held hostage by the slowest peer.
        """
        if self._pool_ready.is_set():
            return {"kind": "status", "ready": True}
        state = "loading" if self._current_placement else "waiting"
        resp: dict = {"kind": "status", "ready": False, "state": state}
        if state != "loading" or not self._current_placement:
            return resp

        chat_acc = None
        asker = self.participants.get(asker_wid) if asker_wid else None
        if asker is not None:
            chat_acc = asker.account_id

        local_pair = None
        if chat_acc is not None:
            for seg in self._current_placement:
                wseg = self.participants.get(seg[0])
                if wseg is None or wseg.account_id != chat_acc:
                    continue
                pair = self._load_progress.get(seg[0])
                if pair and pair[1] > 0:
                    local_pair = pair
                    break

        if local_pair is not None:
            n, t = local_pair
            if n < t:
                resp["progress"] = n / t
                resp["bytes_now"] = n
                resp["bytes_total"] = t
                return resp
            # Local download is done — switch the user-facing message
            # from "Loading the model" to "Waiting for other machines"
            # so a fast machine doesn't appear stuck at 100% while
            # the pool waits on a slower peer.
            resp["state"] = "loading_peers"
            return resp

        # Chat-only client (no companion worker on this account) isn't
        # downloading anything locally — showing a bar of *someone
        # else's* progress would be misleading. Just say we're waiting
        # for the pool's other machines; no bar.
        resp["state"] = "loading_peers"
        return resp

    def _not_ready_status_response(self) -> dict:
        """Tell the chat client which of the two non-ready states the
        pool is in. Only two values; the chat picks the friendly text.

        Detail (RAM numbers, model id, worker counts) stays in the hub
        log — never in the user-facing response.
        """
        compute_workers = [p for p in self.participants.values() if p.compute]
        # If we've already decided a placement, workers are loading.
        # Otherwise the pool either has no workers or can't fit the
        # model — both look the same to the user: "need more machines".
        state = "loading" if self._current_placement else "waiting"
        return {"kind": "not_ready", "state": state}

    async def reset_conversation_on_workers(self, conv_id: str) -> None:
        """Broadcast `reset_conversation` to every compute worker. Used by
        both the chat-client /reset path and the HTTP API after each
        stateless request, so workers' per-conv KV caches don't accumulate."""
        async with self._pool_lock:
            workers = [p for p in self.participants.values() if p.compute]
        await asyncio.gather(
            *[w.conn.call("reset_conversation", conv_id=conv_id) for w in workers],
            return_exceptions=True,
        )

    async def run_turn(
        self,
        conv: Conversation,
        max_new_tokens: int = DEFAULT_MAX_NEW_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        on_token=None,
        is_alive=None,
        querier_account: str | None = None,
    ) -> tuple[str, dict]:
        """Run one chat turn against `conv.history` via delegation.

        The hub no longer does per-token compute. It picks a compute
        worker via the scheduler (cheap mode: self-utilization wins when
        a querier-owned worker exists; otherwise the highest-TPS worker
        wins via the secondary tiebreak), sends `delegate_turn`, and
        forwards the worker's `delegate_token` notifications to the chat
        client (via the `on_token` callback registered for this conv).
        """
        if is_alive is not None and not is_alive():
            raise RuntimeError("requester disconnected")
        await self._pool_ready.wait()

        head, my_layer_end, chain_payload = self._build_chain_spec(
            querier_account=querier_account,
        )
        if head is None:
            raise RuntimeError("no ready compute worker to delegate the turn to")
        worker = head

        # Register the on_token callback so this hub instance can forward
        # `delegate_token` notifications coming back from the worker. The
        # routing key is the chat client's conv.id.
        if on_token is not None:
            self._token_routers[conv.id] = on_token
        try:
            t0 = time.perf_counter()
            call_payload = dict(
                conv_id=conv.id,
                history=conv.history,
                max_tokens=max_new_tokens,
                temperature=temperature,
            )
            if chain_payload:
                call_payload["my_layer_end"] = my_layer_end
                call_payload["chain"] = chain_payload
            resp = await worker.conn.call("delegate_turn", **call_payload)
            wall_ms = (time.perf_counter() - t0) * 1000
            # delegate_token notifications dispatch as fire-and-forget
            # tasks; the response (delegate_done) may resolve before the
            # last token task has run. Yield so trailing tasks complete
            # before we tear down the router and the client misses tokens.
            await asyncio.sleep(0)
        finally:
            self._token_routers.pop(conv.id, None)

        if resp.get("kind") != "delegate_done":
            raise RuntimeError(resp.get("error", "delegate_turn failed"))

        # Capture the worker's reported active_params for ledger CU math.
        # The hub itself no longer loads the model, so this is the only
        # path that surfaces param count to the attribution code.
        active = int(resp.get("active_params") or 0)
        if active and not self.active_params:
            self.active_params = active

        return resp["text"], {
            "prefill_tokens": int(resp.get("prefill_tokens", 0)),
            "prefill_ms": float(resp.get("prefill_ms", 0.0)),
            "decode_tokens": int(resp.get("decode_tokens", 0)),
            "decode_ms": float(resp.get("elapsed_ms", wall_ms)),
            "worker_id": worker.id,
        }

    # ─────────────────────── delegation helpers ──────────────────────

    def _build_chain_spec(
        self,
        querier_account: str | None = None,
    ) -> "tuple[Participant | None, int, list[dict]]":
        """Pick head + chain for this turn.

        If a partition placement is locked in (DOTS_FORCE_PARTITION), use
        that directly so workers run only the layers they actually loaded.
        Otherwise fall back to scheduler-picked single-worker delegation.

        Two outputs:
          • `head` is the worker we send `delegate_turn` to. It hosts embed,
            its own decoder-layer slice, final norm, lm_head, and the
            sampler.
          • `chain` is a list of `{worker_id, host, port, layer_start,
            layer_end}` describing the downstream workers, in pipeline
            order, that run their layer slices on hidden states received
            from the head.

        Single-worker case: chain is empty; head runs the whole model.
        Multi-worker: scheduler decides the split via the decoder-layer
        unit graph. Cheap mode + querier_account → self-utilization on
        the head; non-dominated tiebreak picks the highest-TPS chain.
        """
        candidates = [
            p for p in self.participants.values()
            if p.compute and p.ready
        ]
        if not candidates:
            return None, 0, []

        # If the hub locked a *true* multi-segment partition placement
        # (each worker holds only its layer slice), use it directly. We
        # detect this by the head segment's end being less than
        # n_layers — otherwise it's a "full to everyone" placement and
        # the chain path doesn't apply.
        partition_active = (
            self._current_placement
            and len(self._current_placement) >= 2
            and self._current_placement[0][2] < self.n_layers
        )
        if partition_active:
            ready_ids = {p.id for p in candidates}
            if all(seg[0] in ready_ids for seg in self._current_placement):
                head_id, _, head_end, _ = self._current_placement[0]
                head = next((p for p in candidates if p.id == head_id), None)
                if head is not None:
                    chain_payload: list[dict] = []
                    for worker_id, start, end, _ in self._current_placement[1:]:
                        p = next((p for p in candidates if p.id == worker_id), None)
                        if p is None or p.peer_listen_port is None:
                            chain_payload = []
                            break
                        chain_payload.append({
                            "worker_id": worker_id,
                            "host": p.peer_host,
                            "port": int(p.peer_listen_port),
                            "layer_start": int(start),
                            "layer_end": int(end),
                        })
                    if chain_payload or len(self._current_placement) == 1:
                        return head, int(head_end), chain_payload

        # Single-worker fast path: only one candidate ⇒ no chain needed.
        if len(candidates) == 1 or self._geometry is None or self.n_layers <= 1:
            head = self._pick_delegate_worker(querier_account=querier_account)
            return head, (head.layer_range[1] if head and head.layer_range else self.n_layers), []

        # Multi-worker: ask the scheduler to place decoder-layer units.
        workers_snapshot, latency = self.pool_view.snapshot()
        ready_ids = {p.id for p in candidates}
        workers_snapshot = [w for w in workers_snapshot if w.worker_id in ready_ids]

        geom = self._geometry
        # Use a small per-layer FLOP estimate so the cost model reflects
        # real work (and the scheduler tiebreaks reasonably). RAM is set
        # to 0 because every ready worker already has the full model
        # loaded in v1 — they're not constrained by additional RAM here.
        # Once partial loading lands, ram_required_gb gets the real number.
        per_layer_params = 16 * geom.hidden_size ** 2
        per_layer_flops = float(2 * per_layer_params)
        units = [
            WorkUnit(unit_id=f"block_{i}",
                     ram_required_gb=0.0,
                     flops_per_token=per_layer_flops)
            for i in range(geom.n_layers)
        ]
        placement = schedule(
            units, workers_snapshot, latency,
            querier_account=querier_account,
            mode="cheap",
            max_segments=len(workers_snapshot),
        )
        if placement is None or not placement.segments:
            head = self._pick_delegate_worker(querier_account=querier_account)
            return head, geom.n_layers, []

        # Convert segments (worker_id, [block_i, block_i+1, ...]) into
        # contiguous layer ranges.
        segs: list[tuple[str, int, int]] = []
        cursor = 0
        for worker_id, unit_ids in placement.segments:
            count = len(unit_ids)
            segs.append((worker_id, cursor, cursor + count))
            cursor += count

        head_id, _, head_end = segs[0]
        head = next((p for p in candidates if p.id == head_id), None)
        if head is None:
            head = candidates[0]

        chain_payload: list[dict] = []
        for worker_id, start, end in segs[1:]:
            p = next((p for p in candidates if p.id == worker_id), None)
            if p is None or p.peer_listen_port is None:
                # Chain peer unreachable — fall back to single-worker.
                return head, geom.n_layers, []
            chain_payload.append({
                "worker_id": worker_id,
                "host": p.peer_host,
                "port": int(p.peer_listen_port),
                "layer_start": start,
                "layer_end": end,
            })
        return head, head_end, chain_payload

    def _pick_delegate_worker(
        self,
        querier_account: str | None = None,
    ) -> "Participant | None":
        """Choose a compute worker via the resource-aware scheduler.

        Uses a single virtual work unit representing the full model. With
        cheap mode the scheduler's non-dominated set degenerates to:
          • self-utilization (CU=0) when querier owns a ready worker
          • highest-TPS worker otherwise (cheap mode tiebreaks on TPS)
        That's the policy we want for single-worker delegation.
        """
        candidates = [
            p for p in self.participants.values()
            if p.compute and p.ready
        ]
        if not candidates:
            return None

        workers_snapshot, latency = self.pool_view.snapshot()
        # Restrict the snapshot to currently-ready compute workers — the
        # scheduler shouldn't try to place on a still-loading worker.
        ready_ids = {p.id for p in candidates}
        workers_snapshot = [w for w in workers_snapshot if w.worker_id in ready_ids]
        if not workers_snapshot:
            return candidates[0]

        # In v1 the entire model lives on a single worker (no peer
        # pipeline yet), so we feed the scheduler one virtual unit sized
        # to fit on any ready worker. flops_per_token uses the model's
        # active_params (reported in worker_ready) so the Pareto cost
        # estimate reflects real work.
        flops = 2 * (self.active_params or 1)
        unit = WorkUnit(
            unit_id="model",
            ram_required_gb=0.0,  # already loaded; don't double-count
            flops_per_token=float(flops),
        )
        placement = schedule(
            [unit], workers_snapshot, latency,
            querier_account=querier_account,
            mode="cheap",
            max_segments=1,
        )
        if placement is None:
            return candidates[0]

        chosen_id = placement.segments[0][0]
        chosen = next((p for p in candidates if p.id == chosen_id), None)
        return chosen or candidates[0]

    def _on_worker_ready(self, worker_id: str, msg: dict) -> dict | None:
        """Mark a worker as ready after it finished loading the model.

        The worker streams this notification once its background
        `mlx_load` completes. The hub starts routing turns to it after.
        It also carries the model geometry (n_layers/hidden_size/vocab_size)
        so the scheduler can build a real unit graph for placement.
        """
        p = self.participants.get(worker_id)
        if p is None:
            return None
        p.ready = True
        n_layers = int(msg.get("n_layers") or 0)
        if n_layers and not self.n_layers:
            self.n_layers = n_layers
        active = int(msg.get("active_params") or 0)
        if active and not self.active_params:
            self.active_params = active
        geom = msg.get("geometry")
        if isinstance(geom, dict) and self._geometry is None:
            try:
                self._geometry = ModelGeometry(
                    n_layers=int(geom["n_layers"]),
                    hidden_size=int(geom["hidden_size"]),
                    vocab_size=int(geom["vocab_size"]),
                )
            except (KeyError, TypeError, ValueError):
                log.debug("ignoring malformed geometry payload", exc_info=True)
        # Synthesize a layer_range so the ledger attribution path keeps
        # working. Each worker hosts the entire model in v1, so the range
        # is the full set.
        # `layer_range` was set in _broadcast_load_units to the actual
        # assignment (which may be a slice). Only fall back to "the
        # whole model" if no load_units was ever sent (legacy path).
        if self.n_layers and p.layer_range is None:
            p.layer_range = (0, self.n_layers)
        was_ready = self._pool_ready.is_set()
        self._pool_ready.set()
        a, b = p.layer_range or (0, self.n_layers)
        ui.notice(f"worker·{p.short_id} ({p.display_owner}) ready "
                  f"(layers [{a}:{b}])")
        # Log a single "pool ready" line once every assigned worker has
        # reported back, so the operator knows queries will now succeed.
        compute_workers = [w for w in self.participants.values() if w.compute]
        if compute_workers and all(w.ready for w in compute_workers) and not was_ready:
            ui.notice("pool ready — accepting queries")
        # Now that this worker can serve, kick off pairwise RTT probes
        # between it and every other ready compute worker. Each pair
        # produces one cell in `pool_view.latency`.
        if p.compute and p.peer_listen_port is not None:
            asyncio.create_task(self._probe_pairs_for(worker_id))
        return None

    async def _probe_pairs_for(self, worker_id: str) -> None:
        """For each existing ready compute worker, ask both sides to
        ping each other and record the median RTT into pool_view."""
        async with self._pool_lock:
            me = self.participants.get(worker_id)
            if me is None or not me.compute or not me.ready:
                return
            others = [
                p for p in self.participants.values()
                if (p.id != worker_id and p.compute and p.ready
                    and p.peer_listen_port is not None)
            ]
        for other in others:
            asyncio.create_task(self._probe_one(me.id, other.id,
                                                 other.peer_host,
                                                 other.peer_listen_port))
            asyncio.create_task(self._probe_one(other.id, me.id,
                                                 me.peer_host,
                                                 me.peer_listen_port))

    async def _probe_one(
        self,
        source_id: str,
        target_id: str,
        target_host: str,
        target_port: int,
    ) -> None:
        p = self.participants.get(source_id)
        if p is None:
            return
        try:
            resp = await p.conn.call(
                "probe_peer",
                target_id=target_id,
                target_host=target_host,
                target_port=int(target_port),
                samples=3,
            )
        except Exception:
            log.debug("probe call failed (%s → %s)", source_id, target_id,
                      exc_info=True)
            return
        if resp.get("kind") != "probe_result" or "error" in resp:
            return
        try:
            rtt_p50 = float(resp["rtt_p50_ms"])
            self.pool_view.record_probe(
                source_id, target_id, rtt_p50,
                float(resp.get("rtt_p99_ms", 0.0)),
                int(resp.get("samples", 1)),
            )
            self._log_latency_mesh()
        except Exception:
            log.debug("could not record probe result", exc_info=True)

    def _log_latency_mesh(self) -> None:
        """Pretty-print the current pairwise RTT matrix between compute
        workers. Called whenever a probe updates a cell so the operator
        can watch the mesh fill in as workers join."""
        workers = sorted(
            (p for p in self.participants.values()
             if p.compute and p.peer_listen_port is not None),
            key=lambda p: p.id,
        )
        if len(workers) < 2:
            return
        # Column header / label width: max of "X→" label + short_id length
        labels = [f"{p.short_id} ({p.display_owner})" for p in workers]
        col_w = max(len(lab) for lab in labels) + 1
        header = " " * (col_w + 2) + "  ".join(f"{lab:>{col_w}}" for lab in labels)
        rows = [header]
        for row_p, row_label in zip(workers, labels):
            cells = []
            for col_p in workers:
                if row_p.id == col_p.id:
                    cells.append(f"{'—':>{col_w}}")
                    continue
                rtt = self.pool_view.get_rtt(row_p.id, col_p.id)
                if rtt is None:
                    cells.append(f"{'·':>{col_w}}")
                else:
                    cells.append(f"{rtt:>{col_w - 3}.1f} ms")
            rows.append(f"{row_label:>{col_w}}  " + "  ".join(cells))
        ui.notice("latency mesh (ms):\n  " + "\n  ".join(rows))

    async def _on_delegate_token(self, worker_id: str, msg: dict) -> dict | None:
        """A compute worker is streaming a token mid-turn. Forward to the
        chat client whose conv_id is registered with this token."""
        conv_id = msg.get("conv_id")
        text = msg.get("text", "")
        if not conv_id:
            return None
        cb = self._token_routers.get(conv_id)
        if cb is not None:
            try:
                await cb(text)
            except Exception:
                log.debug("on_token callback failed", exc_info=True)
        return None

    # ─────────────────────────── scheduler integration ──────────────────

    def plan_placement(
        self,
        querier_account: str | None = None,
        mode: str = "cheap",
        layers_per_block: int = 1,
        max_segments: int | None = None,
    ) -> "Placement | None":
        """Run the resource-aware scheduler on the current pool snapshot.

        Used by callers that want to inspect *what* the scheduler would
        pick (e.g. observability, the `dots credits` API, the future
        multi-worker distributed compute plane). The live data plane uses
        `_pick_delegate_worker` until multi-worker placement lands.

        Returns the Pareto-optimal placement for `mode`, or None if no
        feasible placement exists.
        """
        if self._geometry is None:
            return None
        units = units_for_geometry(self._geometry, layers_per_block=layers_per_block)
        workers, latency = self.pool_view.snapshot()
        if not workers:
            return None
        return schedule(
            units, workers, latency,
            querier_account=querier_account,
            mode=mode,
            max_segments=max_segments,
        )

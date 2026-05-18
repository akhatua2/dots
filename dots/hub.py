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
    """A connected worker. Each one is also a chatter with their own conv."""
    id: str                                    # worker's stable UUID
    addr: str
    free_ram_gb: float
    conn: PeerConnection
    compute: bool = True                       # contributes layers to the pool
    max_load_gb: float | None = None           # None == no cap (vestigial)
    layer_range: tuple[int, int] | None = None
    ready: bool = False
    # The human-owner account this worker contributes to. CU and the
    # future ledger get attributed against this, not against the
    # per-process `id` above. Multiple Participants can share an account.
    account_id: str | None = None
    account_alias: str | None = None
    # Where this worker accepts incoming peer connections (other workers
    # talk to it on this port for probes + the future multi-worker
    # pipeline). The host part comes from the TCP socket peername; the
    # port comes from HELLO's `peer_listen_port`. None means the worker
    # didn't open a peer server (chat-only clients).
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


class TopologyChanged(Exception):
    """Raised inside a turn when the pool changed under our feet."""


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

    # ───────────────────────────── lifecycle ─────────────────────────────

    async def start(self) -> None:
        # In the delegation architecture the hub never loads a model.
        # Workers load the full model themselves and announce via
        # `worker_ready`. The hub just orchestrates and writes the ledger.
        ui.notice(f"hub starting · default model = {self.model_id}")
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
        free_ram = float(hello_msg.get("free_ram_gb", 0.0))
        max_load_raw = hello_msg.get("max_load_gb")
        max_load_gb = float(max_load_raw) if max_load_raw is not None else None
        # Default to compute=True if the client didn't say (so pre-PR-5
        # workers keep behaving the same way).
        compute = bool(hello_msg.get("compute", True))
        account_id = hello_msg.get("account_id")
        account_id = account_id if isinstance(account_id, str) and account_id else None
        account_alias = hello_msg.get("account_alias")
        account_alias = (account_alias if isinstance(account_alias, str)
                         and account_alias else None)

        # Extract the new-style resource declaration. Falls back to the
        # legacy free_ram_gb if the worker is an older build.
        resource_decl = resource_decl_from_hello(hello_msg, worker_id=worker_id)

        # If a worker with this identity is already in the pool (process
        # restart whose old connection hasn't been read-detected yet), close
        # the stale one and replace it.
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
                id=worker_id, addr=addr_str, free_ram_gb=free_ram,
                compute=compute, max_load_gb=max_load_gb, conn=conn,
                account_id=account_id, account_alias=account_alias,
                peer_listen_port=peer_port,
            )
            self.participants[worker_id] = participant
            if resource_decl is not None:
                self.pool_view.add_or_update(worker_id, resource_decl)
        cap_str = f", cap={max_load_gb:.1f}GB" if max_load_gb is not None else ""
        role = "worker" if compute else "client"
        ui.notice(f"{role}·{participant.short_id} ({participant.display_owner}) "
                  f"joined ({addr_str}, free_ram={free_ram:.1f}GB{cap_str})")
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
            left_role = "worker" if participant.compute else "client"
            ui.notice(f"{left_role}·{participant.short_id} "
                      f"({participant.display_owner}) left")
            await self._on_pool_changed()

    # ─────────────────────────── re-plan on change ──────────────────────

    async def _on_pool_changed(self) -> None:
        """In the delegation architecture, the hub no longer plans layer
        ranges or broadcasts LOAD_SHARD. Compute workers load the full
        model autonomously and notify `worker_ready` when they're done.

        On topology change, we just clear pool_ready and let the next
        worker_ready notification re-set it.
        """
        async with self._pool_lock:
            self._topology_gen += 1
            workers = [p for p in self.participants.values() if p.compute]
            self._pool_ready.clear()
            if not workers:
                ui.notice("no compute workers — waiting")
                return
            # If any compute worker is already loaded, immediately mark
            # pool ready so chat clients connecting in the middle of a
            # load don't have to wait for the next worker_ready event.
            if any(p.ready for p in workers):
                self._pool_ready.set()

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
            return {"kind": "error", "error": "pool not ready"}

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
        except TopologyChanged:
            if conv.history and conv.history[-1].get("role") == "user":
                conv.history.pop()
            return {"kind": "error", "error": "topology changed during turn"}
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
        if self.n_layers:
            p.layer_range = (0, self.n_layers)
        self._pool_ready.set()
        ui.notice(f"worker·{p.short_id} ({p.display_owner}) ready "
                  f"({self.n_layers} layers loaded)")
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
            self.pool_view.record_probe(
                source_id, target_id,
                float(resp["rtt_p50_ms"]),
                float(resp.get("rtt_p99_ms", 0.0)),
                int(resp.get("samples", 1)),
            )
        except Exception:
            log.debug("could not record probe result", exc_info=True)

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

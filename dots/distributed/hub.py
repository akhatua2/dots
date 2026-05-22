"""Hub: pure orchestrator. The pool is the set of connected workers.

The hub itself does no model compute. It:

  • Accepts worker TCP connections. On HELLO with a ResourceDecl, the
    worker is added to the pool. The hub then selects a backend supported
    by the pool and sends each worker its model assignment.
  • Maintains one Conversation per chat-client participant.
  • On a chat-client PROMPT, picks a compute worker via the scheduler,
    sends `delegate_turn(conv_id, history, max_tokens, temperature)`, and
    forwards each `delegate_token` notification back to the chat client
    as a `token` notification. When the worker replies `delegate_done`,
    the turn is finished and the ledger is updated.

Empty pool → hub returns errors until a worker joins.

Architectural pieces:
  • dots.placement.scheduler — placement algorithm (workers + latency)
  • dots.placement.sharding  — strategies (PP / TP / EP); v1 ships PP
  • dots.compute_plane — abstraction the model talks to (wraps scheduler + sharding)
  • dots.distributed.pool — hub-side state (resources + latency matrix)
  • dots.placement.work_units — per-token DAG (embed, layers, lm_head, sample)
  • dots.backends      — actual runtime primitives owned by each worker
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, replace

from .. import ui
from ..accounting.ledger import Ledger
from ..backends.registry import get_backend_runtime, SUPPORTED_BACKENDS
from .peer import PeerConnection
from .pool import Pool, resource_decl_from_hello
from ..placement.scheduler import Placement, WorkUnit, schedule
from ..system.util import dots_home
from ..placement.work_units import ModelGeometry, units_for_geometry

log = logging.getLogger("dots.hub")

HELLO_TIMEOUT_SEC = 30.0
DEFAULT_MAX_NEW_TOKENS = 256
DEFAULT_TEMPERATURE = 0.7
PLACEMENT_RAM_MARGIN_GB = 1.0
HEAD_WORKSPACE_RAM_GB = 0.5
TRANSFORMER_LAYER_FLOP_PARAM_FACTOR = 16
_PLACEMENT_RAM_MARGIN_ENV = "DOTS_PLACEMENT_RAM_MARGIN_GB"
_HEAD_WORKSPACE_RAM_ENV = "DOTS_HEAD_WORKSPACE_RAM_GB"


def _float_policy(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return default if raw is None else float(raw)


@dataclass
class Conversation:
    id: str
    history: list[dict] = field(default_factory=list)
    token_count: int = 0


@dataclass
class ModelWorkerState:
    """Hub-side readiness for one worker serving one model."""

    layer_range: tuple[int, int] | None = None
    ready: bool = False
    backend: str | None = None


@dataclass
class ModelDeployment:
    """Hub-side serving state for one model id."""

    model_id: str
    n_layers: int = 0
    active_params: int = 0
    geometry: ModelGeometry | None = None
    selected_backend: str | None = None
    current_placement: list[tuple[str, int, int, bool]] = field(default_factory=list)
    load_progress: dict[str, tuple[int, int]] = field(default_factory=dict)
    worker_states: dict[str, ModelWorkerState] = field(default_factory=dict)
    pool_ready: asyncio.Event = field(default_factory=asyncio.Event)
    # Wall-clock of the last turn served for this model; drives LRU order
    # when the hub evicts idle deployments to reclaim RAM.
    last_used_at: float = 0.0


@dataclass
class Participant:
    """A connected worker (or chat-only client) and its hub-side state."""

    id: str  # worker's stable UUID
    addr: str
    conn: PeerConnection
    compute: bool = True  # contributes layers to the pool
    layer_range: tuple[int, int] | None = None
    ready: bool = False
    backend: str | None = None
    # The human-owner account this worker contributes to. CU and the
    # ledger get attributed against this, not against the per-process
    # `id` above. Multiple Participants can share an account.
    account_id: str | None = None
    account_alias: str | None = None
    # For chat clients: the model this client intends to query. Marks
    # that model as "in use" so the hub won't evict it to free RAM for
    # another. None for compute-only workers (they don't query).
    requested_model_id: str | None = None
    # Where this worker accepts incoming peer connections (other workers
    # talk to it on this port for probes + the multi-worker chain).
    # `peer_listen_port` comes from HELLO; None means chat-only.
    # `peer_listen_host` is the routable IP the WORKER picked for itself
    # (Tailscale CGNAT preferred, then RFC1918 LAN, then loopback).
    peer_listen_port: int | None = None
    peer_listen_host: str | None = None
    conv: Conversation = field(
        default_factory=lambda: Conversation(id=str(uuid.uuid4()))
    )

    @property
    def peer_host(self) -> str:
        """Routable host for incoming peer connections.

        Compute workers must advertise this explicitly. Inferring it
        from the hub connection is wrong for cross-machine loopback cases.
        """
        if not self.peer_listen_host:
            raise RuntimeError("worker did not advertise peer_listen_host")
        return self.peer_listen_host

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
            short = (
                self.account_id[4:]
                if self.account_id.startswith("acc-")
                else self.account_id
            )
            return short[:8]
        return "anon"


class Hub:
    def __init__(
        self,
        port: int,
        model_id: str | None = None,
        host: str = "0.0.0.0",
        http_port: int | None = None,
        ledger_path=None,
    ):
        self.host = host
        self.port = port
        self.http_port = http_port
        self.deployments: dict[str, ModelDeployment] = {}
        # The hub is model-agnostic: it serves no model of its own and
        # never picks one for a client. The querier (chat) names the
        # model on every request; the hub spins up a deployment per id on
        # demand. `model_id` here is an optional seed used only by tests
        # that want a pre-existing deployment. `self.model_id` is the
        # transient "active deployment" pointer the scheduler switches per
        # turn via `_activate_model` — not a served-model setting.
        self.model_id = model_id
        if model_id is not None:
            self._deployment_for(model_id)

        # The hub does not load a model in the delegation architecture.
        # `n_layers` and `active_params` come in from workers via the
        # `worker_ready` notification and are used by the ledger for
        # FLOP-CU attribution.

        self.participants: dict[str, Participant] = {}
        self._pool_lock = asyncio.Lock()
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
        # conv_id → async callback for streaming tokens back to the chat
        # client during a delegate_turn. Populated in `run_turn` and
        # consulted in `_on_delegate_token`.
        self._token_routers: dict[str, "asyncio.Future | object"] = {}
        # Per-conv counter for tokens emitted so far. Used by the
        # run_turn watchdog to log progress on long turns + by
        # _on_delegate_token to detect the first-token arrival.
        self._turn_token_counts: dict[str, int] = {}
        self._model_turn_lock = asyncio.Lock()

    def _deployment_for(self, model_id: str | None = None) -> ModelDeployment:
        selected = model_id or self.model_id
        deployment = self.deployments.get(selected)
        if deployment is None:
            deployment = ModelDeployment(model_id=selected)
            self.deployments[selected] = deployment
        return deployment

    def _model_worker_state(
        self,
        worker_id: str,
        model_id: str | None = None,
    ) -> ModelWorkerState:
        deployment = self._deployment_for(model_id)
        state = deployment.worker_states.get(worker_id)
        if state is None:
            state = ModelWorkerState()
            deployment.worker_states[worker_id] = state
        return state

    def _sync_legacy_participant_state(
        self,
        participant: Participant,
        model_id: str | None = None,
    ) -> None:
        """Keep old single-model fields coherent for compatibility.

        Tests and a few UI paths still read Participant.ready/backend/range
        directly. The real serving state now lives per deployment, but
        mirroring the active model avoids a broad unrelated rewrite.
        """
        state = self._deployment_for(model_id).worker_states.get(participant.id)
        if state is None:
            return
        participant.layer_range = state.layer_range
        participant.ready = state.ready
        participant.backend = state.backend

    @property
    def n_layers(self) -> int:
        return self._deployment_for().n_layers

    @n_layers.setter
    def n_layers(self, value: int) -> None:
        self._deployment_for().n_layers = value

    @property
    def active_params(self) -> int:
        return self._deployment_for().active_params

    @active_params.setter
    def active_params(self, value: int) -> None:
        self._deployment_for().active_params = value

    @property
    def _geometry(self) -> ModelGeometry | None:
        return self._deployment_for().geometry

    @_geometry.setter
    def _geometry(self, value: ModelGeometry | None) -> None:
        self._deployment_for().geometry = value

    @property
    def _selected_backend(self) -> str | None:
        return self._deployment_for().selected_backend

    @_selected_backend.setter
    def _selected_backend(self, value: str | None) -> None:
        self._deployment_for().selected_backend = value

    @property
    def _current_placement(self) -> list[tuple[str, int, int, bool]]:
        return self._deployment_for().current_placement

    @_current_placement.setter
    def _current_placement(self, value: list[tuple[str, int, int, bool]]) -> None:
        self._deployment_for().current_placement = value

    @property
    def _load_progress(self) -> dict[str, tuple[int, int]]:
        return self._deployment_for().load_progress

    @_load_progress.setter
    def _load_progress(self, value: dict[str, tuple[int, int]]) -> None:
        self._deployment_for().load_progress = value

    @property
    def _pool_ready(self) -> asyncio.Event:
        return self._deployment_for().pool_ready

    def _activate_model(self, model_id: str) -> None:
        self._deployment_for(model_id)
        self.model_id = model_id

    # ───────────────────────────── lifecycle ─────────────────────────────

    async def start(self) -> None:
        # The hub is model-agnostic: it loads nothing and serves no model
        # of its own. Queriers name the model on each request and the hub
        # spins up a deployment per id across the pool on demand.
        ui.notice("hub starting · model-agnostic (queriers choose the model)")
        server = await asyncio.start_server(self._on_connect, self.host, self.port)
        addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
        ui.notice(f"hub listening on {addrs} — waiting for workers to join")
        self.started.set()

        # OpenAI-compatible HTTP API alongside the worker TCP server. On
        # by default; users can pass --http-port 0 to disable. Lives only
        # for as long as the hub does; cancellation tears it down.
        http_task: asyncio.Task | None = None
        if self.http_port:
            from ..app.api import HttpApi

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
        """Read backend-neutral model metadata so the scheduler can plan
        placements before workers load model weights.
        """
        candidates = self._backend_candidates_from_pool()
        if not candidates:
            return
        errors: list[str] = []
        for backend_name in candidates:
            try:
                metadata = await get_backend_runtime(backend_name).metadata_for(
                    self.model_id
                )
            except Exception as e:
                errors.append(f"{backend_name}: {e}")
                continue
            self._selected_backend = backend_name
            self._geometry = metadata.geometry
            self.n_layers = metadata.geometry.n_layers
            return
        ui.error(
            f"could not load model metadata for '{self.model_id}' with any "
            f"available worker backend ({'; '.join(errors)}). "
            f"check the model id — common typos include 30B (use 32B) "
            f"or omitted '-4bit' / '-Instruct' suffixes."
        )

    def _backend_candidates_from_pool(self) -> list[str]:
        offered: set[str] = set()
        for participant in self.participants.values():
            if not participant.compute:
                continue
            decl = self.pool_view.resources.get(participant.id)
            if decl is not None:
                offered.update(decl.backends)
        return [name for name in SUPPORTED_BACKENDS if name in offered]

    def _worker_supports_selected_backend(self, participant: Participant) -> bool:
        if self._selected_backend is None:
            return False
        decl = self.pool_view.resources.get(participant.id)
        return decl is not None and self._selected_backend in decl.backends

    def _selected_backend_still_available(self) -> bool:
        if self._selected_backend is None:
            return False
        return any(
            self._worker_supports_selected_backend(p)
            for p in self.participants.values()
            if p.compute
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
            (
                p
                for p in self.participants.values()
                if p.compute and self._worker_supports_selected_backend(p)
            ),
            key=lambda p: p.id,
        )
        if not compute_workers:
            return []

        deployment = self._deployment_for()
        reserved_ram = self._reserved_ram_by_worker(exclude_model_id=self.model_id)
        model_ram_gb = self._estimate_model_ram_gb()
        head_overhead_gb = self._estimate_head_overhead_gb()
        self._estimate_layer_ram_gb()
        assert self._geometry.layer_ram_gb is not None
        # Small margin so we never assign right up to a worker's edge.
        margin_gb = _float_policy(
            _PLACEMENT_RAM_MARGIN_ENV,
            PLACEMENT_RAM_MARGIN_GB,
        )

        def free_ram(p: "Participant") -> float:
            decl = self.pool_view.resources.get(p.id)
            if decl is None:
                return 0.0
            return max(0.0, decl.ram_gb_free - reserved_ram.get(p.id, 0.0))

        # Default: every worker that can fit the model holds a full
        # copy. This gives each querier self-utilization — their own
        # machine answers their queries with zero network. Workers that
        # can't fit the full model stay idle and can only chat (their
        # queries route to whichever loaded worker the scheduler picks).
        fitters = [
            p for p in compute_workers if free_ram(p) >= model_ram_gb + margin_gb
        ]
        if fitters:
            # Don't cache — additional workers joining later should also
            # load (they fit). New workers get the assignment on the
            # next pool change.
            out = [(p.id, 0, self.n_layers, True) for p in fitters]
            deployment.current_placement = out
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
            2 * TRANSFORMER_LAYER_FLOP_PARAM_FACTOR * (self._geometry.hidden_size**2)
        )
        units = []
        for i in range(self.n_layers):
            exact_layer_ram = self._geometry.layer_ram_gb[i]
            unit_ram = exact_layer_ram + (head_overhead_gb if i == 0 else 0.0)
            units.append(
                WorkUnit(
                    unit_id=f"block_{i}",
                    ram_required_gb=unit_ram + (margin_gb if i == 0 else 0),
                    flops_per_token=per_layer_flops,
                )
            )

        workers_snapshot, latency = self.pool_view.snapshot()
        ready_ids = {p.id for p in compute_workers}
        workers_snapshot = [
            replace(
                w,
                ram_gb_free=max(
                    0.0,
                    w.ram_gb_free - reserved_ram.get(w.worker_id, 0.0),
                ),
            )
            for w in workers_snapshot
            if w.worker_id in ready_ids
        ]
        if not workers_snapshot:
            deployment.current_placement = []
            return []

        placement = schedule(
            units,
            workers_snapshot,
            latency,
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
            deployment.current_placement = []
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
        deployment.current_placement = out
        return out

    def _assignment_ram_gb(
        self,
        deployment: ModelDeployment,
        layer_start: int,
        layer_end: int,
        needs_head: bool,
    ) -> float:
        geometry = deployment.geometry
        if geometry is None or geometry.layer_ram_gb is None:
            return 0.0
        ram = sum(geometry.layer_ram_gb[layer_start:layer_end])
        if needs_head:
            if geometry.embed_ram_gb is None:
                return ram
            ram += geometry.embed_ram_gb
            if not geometry.tie_word_embeddings:
                ram += geometry.lm_head_ram_gb or 0.0
            ram += geometry.head_extra_ram_gb
            ram += _float_policy(_HEAD_WORKSPACE_RAM_ENV, HEAD_WORKSPACE_RAM_GB)
        return ram

    def _reserved_ram_by_worker(
        self,
        *,
        exclude_model_id: str | None = None,
    ) -> dict[str, float]:
        """RAM already committed by other model deployments.

        A worker's HELLO reports machine-level available RAM. Once the
        hub serves multiple models, planning a new model against that raw
        number would overbook the pool. This uses exact model metadata
        from existing deployments and subtracts those assignments before
        scheduling another model.
        """
        reserved: dict[str, float] = {}
        for deployment in self.deployments.values():
            if deployment.model_id == exclude_model_id:
                continue
            for worker_id, start, end, needs_head in deployment.current_placement:
                reserved[worker_id] = reserved.get(worker_id, 0.0) + (
                    self._assignment_ram_gb(deployment, start, end, needs_head)
                )
        return reserved

    def _estimate_layer_ram_gb(self) -> float:
        """Average per-decoder-layer weight footprint from exact metadata."""
        if self._geometry is None:
            raise RuntimeError("exact layer RAM metadata is unavailable")
        if self._geometry.components:
            layer_units = [
                c.ram_gb
                for c in self._geometry.components
                if c.kind in {"attention_block", "mamba_block", "decoder_block"}
            ]
            if not layer_units:
                raise RuntimeError("component metadata has no decoder blocks")
            return sum(layer_units) / len(layer_units)
        if (
            not self._geometry.layer_ram_gb
            or len(self._geometry.layer_ram_gb) != self.n_layers
        ):
            raise RuntimeError("exact layer RAM metadata is unavailable")
        return sum(self._geometry.layer_ram_gb) / len(self._geometry.layer_ram_gb)

    def _estimate_head_overhead_gb(self) -> float:
        """Embed + lm_head storage on the head worker (bf16, unquantized)."""
        if self._geometry is None:
            raise RuntimeError("exact head RAM metadata is unavailable")
        if self._geometry.embed_ram_gb is None:
            raise RuntimeError("exact embedding RAM metadata is unavailable")
        if (
            self._geometry.lm_head_ram_gb is None
            and not self._geometry.tie_word_embeddings
        ):
            raise RuntimeError("exact lm_head RAM metadata is unavailable")
        return (
            self._geometry.embed_ram_gb
            + (self._geometry.lm_head_ram_gb or 0.0)
            + self._geometry.head_extra_ram_gb
            + _float_policy(_HEAD_WORKSPACE_RAM_ENV, HEAD_WORKSPACE_RAM_GB)
        )

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
        deployment = self._deployment_for()
        backend_name = self._selected_backend
        if backend_name is None:
            return
        for worker_id, layer_start, layer_end, needs_head in assignments:
            p = self.participants.get(worker_id)
            if p is None or not p.compute:
                continue
            if not self._worker_supports_selected_backend(p):
                continue
            new_range = (int(layer_start), int(layer_end))
            state = deployment.worker_states.setdefault(worker_id, ModelWorkerState())
            # If this worker is already loading (or has loaded) exactly
            # this slice, leave them alone. Re-broadcasting would
            # restart the download.
            if state.layer_range == new_range and state.backend == backend_name:
                continue
            try:
                await p.conn.notify(
                    "load_units",
                    backend=backend_name,
                    model_id=self.model_id,
                    layer_start=new_range[0],
                    layer_end=new_range[1],
                    needs_head=bool(needs_head),
                )
                state.layer_range = new_range
                state.backend = backend_name
                state.ready = False
                self._sync_legacy_participant_state(p)
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
                "(first-time downloads can take a few minutes — "
                "pool will be ready when each worker reports back)"
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
                # The hub advertises no model: the querier names it on
                # each request. (Older clients read these back to default
                # their model — that inheritance is intentionally gone.)
                return {"kind": "hello_ack"}
            wid = wid_holder["id"]
            if kind == "prompt" and wid is not None:
                return await self._on_prompt(wid, msg)
            if kind == "serve_model" and wid is not None:
                return await self._on_serve_model(wid, msg)
            if kind == "reset" and wid is not None:
                return await self._on_reset(wid)
            if kind == "worker_ready" and wid is not None:
                return self._on_worker_ready(wid, msg)
            if kind == "delegate_token" and wid is not None:
                return await self._on_delegate_token(wid, msg)
            if kind == "load_progress" and wid is not None:
                model_id = msg.get("model_id")
                if not isinstance(model_id, str) or not model_id:
                    return None
                deployment = self._deployment_for(model_id)
                bytes_now = int(msg.get("bytes_now") or 0)
                bytes_total = int(msg.get("bytes_total") or 0)
                if bytes_total > 0:
                    # Clamp so workers reporting bytes_now > bytes_total
                    # (rare, but possible if HF API under-estimated) don't
                    # drag the bar past 100%.
                    deployment.load_progress[wid] = (
                        min(bytes_now, bytes_total),
                        bytes_total,
                    )
                return None
            if kind == "status":
                model_id = msg.get("model_id")
                if not isinstance(model_id, str) or not model_id:
                    return {"kind": "error", "error": "model_id is required"}
                await self.ensure_model_started(model_id)
                return self._status_response_for(wid, model_id=model_id)
            return {
                "kind": "error",
                "error": f"unexpected worker-initiated kind: {kind}",
            }

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
        account_alias = (
            account_alias if isinstance(account_alias, str) and account_alias else None
        )

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
            peer_port = (
                int(peer_port_raw)
                if isinstance(peer_port_raw, int) and peer_port_raw > 0
                else None
            )
            advertised_host = hello_msg.get("peer_listen_host")
            peer_host = (
                advertised_host
                if isinstance(advertised_host, str) and advertised_host
                else None
            )
            if compute and (peer_port is None or peer_host is None):
                log.warning(
                    "compute worker %s did not advertise peer host/port; dropping",
                    worker_id,
                )
                await conn.close()
                return
            req_model = hello_msg.get("requested_model_id")
            req_model = req_model if isinstance(req_model, str) and req_model else None
            participant = Participant(
                id=worker_id,
                addr=addr_str,
                compute=compute,
                conn=conn,
                account_id=account_id,
                account_alias=account_alias,
                requested_model_id=req_model,
                peer_listen_port=peer_port,
                peer_listen_host=peer_host,
            )
            self.participants[worker_id] = participant
            if resource_decl is not None:
                self.pool_view.add_or_update(worker_id, resource_decl)
        role = "worker" if compute else "client"
        ui.notice(
            f"{role}·{participant.short_id} ({participant.display_owner}) "
            f"joined ({addr_str}, free_ram={free_ram:.1f}GB)"
        )
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
                # If this worker was part of any locked model placement,
                # clear only that model's placement so the next pool
                # change can rebuild it without disturbing other served
                # models.
                for deployment in self.deployments.values():
                    deployment.worker_states.pop(worker_id, None)
                    if any(seg[0] == worker_id for seg in deployment.current_placement):
                        deployment.current_placement = []
                        deployment.pool_ready.clear()
            left_role = "worker" if participant.compute else "client"
            ui.notice(
                f"{left_role}·{participant.short_id} ({participant.display_owner}) left"
            )
            await self._on_pool_changed()

    # ─────────────────────────── re-plan on change ──────────────────────

    async def _on_pool_changed(self) -> None:
        """Topology change handler.

        Each served model has an independent deployment. A topology
        change starts deployments that do not have a valid placement yet,
        or repairs deployments whose assigned workers left. It does not
        reshuffle a healthy deployment just because a new worker joined:
        that would force reloads and waste pool resources for a possible
        TPS gain we have not explicitly scored.
        """
        async with self._pool_lock:
            self._topology_gen += 1
            workers = [p for p in self.participants.values() if p.compute]
            if not workers:
                for deployment in self.deployments.values():
                    deployment.pool_ready.clear()
                ui.notice("no compute workers — waiting")
                return

        async with self._model_turn_lock:
            for deployment in list(self.deployments.values()):
                await self._ensure_model_started_locked(deployment.model_id)

    async def ensure_model_started(self, model_id: str) -> ModelDeployment:
        """Create or start loading a model deployment if needed."""
        async with self._model_turn_lock:
            return await self._ensure_model_started_locked(model_id)

    async def _ensure_model_started_locked(self, model_id: str) -> ModelDeployment:
        self._activate_model(model_id)
        deployment = self._deployment_for(model_id)
        if self._deployment_placement_intact(deployment):
            self._refresh_pool_ready(model_id=model_id)
            return deployment

        if self._geometry is None or self._selected_backend is None:
            await self._prime_geometry_from_config()
        elif not self._selected_backend_still_available():
            self._selected_backend = None
            self._geometry = None
            self._current_placement = []
            await self._prime_geometry_from_config()

        async with self._pool_lock:
            if self._geometry is None or self._selected_backend is None:
                self._pool_ready.clear()
                return deployment
            assignments = self._assign_units_to_workers()

        # Couldn't place — likely idle deployments are pinning RAM. Unload
        # the ones no client is querying and try once more before giving
        # up (this is what lets you switch models and reclaim the old
        # one's RAM without killing the worker).
        if not assignments and await self._evict_idle_deployments(keep=model_id):
            async with self._pool_lock:
                self._activate_model(model_id)
                assignments = self._assign_units_to_workers()

        if assignments:
            await self._broadcast_load_units(assignments)

        async with self._pool_lock:
            self._refresh_pool_ready(model_id=model_id)
        return deployment

    def _protected_model_ids(self) -> set[str]:
        """Models a connected chat client is currently interested in.
        These are never evicted to free RAM for another model."""
        return {
            p.requested_model_id
            for p in self.participants.values()
            if not p.compute and isinstance(p.requested_model_id, str)
        }

    async def _evict_idle_deployments(self, keep: str) -> bool:
        """Unload every deployment that no connected client is querying,
        to reclaim its RAM for `keep`. LRU order (least-recently-used
        first). Returns True if anything was evicted."""
        protected = self._protected_model_ids()
        protected.add(keep)
        candidates = [
            d
            for d in self.deployments.values()
            if d.model_id not in protected and d.current_placement
        ]
        candidates.sort(key=lambda d: d.last_used_at)
        for deployment in candidates:
            await self._evict_deployment(deployment)
        return bool(candidates)

    async def _evict_deployment(self, deployment: ModelDeployment) -> None:
        """Tell every worker holding this model to unload it, then drop
        the deployment so its RAM stops counting against placement."""
        model_id = deployment.model_id
        worker_ids = {seg[0] for seg in deployment.current_placement}
        ui.notice(
            f"evicting idle model {model_id} to reclaim RAM "
            f"({len(worker_ids)} worker(s))"
        )
        for wid in worker_ids:
            p = self.participants.get(wid)
            if p is None:
                continue
            try:
                await p.conn.call("unload_model", model_id=model_id)
            except Exception:
                log.debug("unload_model failed for %s", wid, exc_info=True)
        deployment.current_placement = []
        deployment.worker_states.clear()
        deployment.load_progress.clear()
        deployment.pool_ready.clear()
        self.deployments.pop(model_id, None)

    def _deployment_placement_intact(self, deployment: ModelDeployment) -> bool:
        if not deployment.current_placement:
            return False
        for worker_id, _, _, _ in deployment.current_placement:
            participant = self.participants.get(worker_id)
            if participant is None or not participant.compute:
                return False
            if deployment.selected_backend is None:
                return False
            decl = self.pool_view.resources.get(worker_id)
            if decl is None or deployment.selected_backend not in decl.backends:
                return False
        if self._can_add_full_replica_without_repartition(deployment):
            return False
        return True

    def _can_add_full_replica_without_repartition(
        self,
        deployment: ModelDeployment,
    ) -> bool:
        """Whether a new worker can hold a whole extra copy.

        This is the only automatic expansion we allow on worker join:
        existing workers keep their assignments, and the new worker loads
        only if it can fit the full model after other served models'
        reservations. True repartitioning still requires a broken
        placement or a future explicit rebalancing policy.
        """
        if (
            deployment.geometry is None
            or deployment.selected_backend is None
            or deployment.n_layers <= 0
        ):
            return False
        if not all(
            start == 0 and end == deployment.n_layers and needs_head
            for _, start, end, needs_head in deployment.current_placement
        ):
            return False
        assigned_ids = {
            worker_id for worker_id, _, _, _ in deployment.current_placement
        }
        model_ram_gb = self._assignment_ram_gb(
            deployment,
            0,
            deployment.n_layers,
            True,
        )
        margin_gb = _float_policy(
            _PLACEMENT_RAM_MARGIN_ENV,
            PLACEMENT_RAM_MARGIN_GB,
        )
        reserved = self._reserved_ram_by_worker(exclude_model_id=deployment.model_id)
        for participant in self.participants.values():
            if not participant.compute or participant.id in assigned_ids:
                continue
            decl = self.pool_view.resources.get(participant.id)
            if decl is None or deployment.selected_backend not in decl.backends:
                continue
            available = decl.ram_gb_free - reserved.get(participant.id, 0.0)
            if available >= model_ram_gb + margin_gb:
                return True
        return False

    def _refresh_pool_ready(self, model_id: str | None = None) -> None:
        """Set or clear `_pool_ready` based on whether every worker in
        the current placement has reported ready. Idempotent — safe to
        call from anywhere a worker's readiness might change."""
        deployment = self._deployment_for(model_id)
        placement_ids = {seg[0] for seg in deployment.current_placement}
        if not placement_ids:
            deployment.pool_ready.clear()
            return
        ready_ids = set()
        for wid, participant in self.participants.items():
            if not participant.compute:
                continue
            state = deployment.worker_states.get(wid)
            if state is None:
                # Compatibility for tests that seed legacy fields.
                ready = (
                    participant.ready
                    and participant.backend == deployment.selected_backend
                )
            else:
                ready = state.ready and state.backend == deployment.selected_backend
            if ready:
                ready_ids.add(wid)
        if placement_ids.issubset(ready_ids):
            deployment.pool_ready.set()
        else:
            deployment.pool_ready.clear()

    # ────────────────────────────── one turn ────────────────────────────

    async def _on_serve_model(self, worker_id: str, msg: dict) -> dict:
        """Register a model deployment without sending a prompt.

        `dots serve MODEL` uses this to ask the pool to prepare a model
        ahead of traffic. If the same model is already being served, this
        reuses the existing deployment instead of creating another copy.
        """
        async with self._pool_lock:
            if worker_id not in self.participants:
                return {"kind": "error", "error": "client no longer in pool"}
        model_id = msg.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            return {"kind": "error", "error": "model_id is required"}
        deployment = await self.ensure_model_started(model_id)
        return {
            "kind": "serve_model_ack",
            "model_id": deployment.model_id,
            "ready": deployment.pool_ready.is_set(),
            "state": "ready"
            if deployment.pool_ready.is_set()
            else ("loading" if deployment.current_placement else "waiting"),
        }

    async def _on_prompt(self, worker_id: str, msg: dict) -> dict:
        """Wire-protocol entry point for chat-client PROMPT messages.
        Owns conversation-history bookkeeping (append user msg / append
        assistant response / rollback on failure) and delegates the
        actual turn to `run_turn`."""
        async with self._pool_lock:
            participant = self.participants.get(worker_id)
        if participant is None:
            return {"kind": "error", "error": "worker no longer in pool"}
        model_id = msg.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            return {"kind": "error", "error": "model_id is required"}
        # Record this client's active interest + mark the model used, so
        # eviction protects it and treats it as most-recently-used.
        participant.requested_model_id = model_id
        await self.ensure_model_started(model_id)
        deployment = self._deployment_for(model_id)
        deployment.last_used_at = time.time()
        if not deployment.pool_ready.is_set():
            return self._not_ready_status_response(model_id=model_id)

        conv = participant.conv
        prompt_preview = (msg["text"] or "")[:60].replace("\n", " ")
        if len(msg["text"] or "") > 60:
            prompt_preview += "…"
        ui.notice(
            f"prompt from {participant.short_id} ({participant.display_owner}): "
            f"{prompt_preview!r}"
        )
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
                model_id=model_id,
            )
        except ConnectionError as e:
            ui.error(f"turn {conv.id[:8]} aborted: worker dropped mid-turn ({e})")
            if conv.history and conv.history[-1].get("role") == "user":
                conv.history.pop()
            return {"kind": "error", "error": f"worker dropped mid-turn: {e}"}
        except Exception as e:
            ui.error(f"turn {conv.id[:8]} failed: {e}")
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
            self._attribute_turn(
                conv=conv,
                participant=participant,
                stats=stats,
                model_id=model_id,
            )
        except Exception:
            log.exception("ledger attribution failed for %s", worker_id)
        return {"kind": "turn_done", **stats}

    def _attribute_turn(
        self,
        conv: "Conversation",
        participant: "Participant",
        stats: dict,
        model_id: str | None = None,
    ) -> None:
        """Record one successful turn in the ledger. Computes the plan
        snapshot from currently-ready compute workers (post-turn state),
        credits each by its layer share, and debits the querier.

        Skips silently if there's no model context yet (test fakes,
        active_params unavailable) or the querier has no account.
        """
        deployment = self._deployment_for(model_id)
        if deployment.active_params <= 0:
            return  # no real model loaded (tests with fakes) → no FLOP unit
        if not participant.account_id:
            log.debug("skipping ledger entry — chat client has no account_id")
            return

        plan: list[tuple[str, str | None, int]] = []
        total_layers_assigned = 0
        for p in self.participants.values():
            state = deployment.worker_states.get(p.id)
            if state is None:
                ready = p.ready
                backend = p.backend
                layer_range = p.layer_range
            else:
                ready = state.ready
                backend = state.backend
                layer_range = state.layer_range
            if not (
                p.compute
                and ready
                and backend == deployment.selected_backend
                and layer_range
                and p.account_id
            ):
                continue
            layers_owned = layer_range[1] - layer_range[0]
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
            model_id=deployment.model_id,
            active_params=deployment.active_params,
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

    def _status_response_for(
        self,
        asker_wid: str | None,
        model_id: str | None = None,
    ) -> dict:
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
        deployment = self._deployment_for(model_id)
        if deployment.pool_ready.is_set():
            return {"kind": "status", "ready": True}
        state = "loading" if deployment.current_placement else "waiting"
        resp: dict = {"kind": "status", "ready": False, "state": state}
        if state != "loading" or not deployment.current_placement:
            return resp

        chat_acc = None
        asker = self.participants.get(asker_wid) if asker_wid else None
        if asker is not None:
            chat_acc = asker.account_id

        local_pair = None
        if chat_acc is not None:
            for seg in deployment.current_placement:
                wseg = self.participants.get(seg[0])
                if wseg is None or wseg.account_id != chat_acc:
                    continue
                pair = deployment.load_progress.get(seg[0])
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

    def _not_ready_status_response(self, model_id: str | None = None) -> dict:
        """Tell the chat client which of the two non-ready states the
        pool is in. Only two values; the chat picks the friendly text.

        Detail (RAM numbers, model id, worker counts) stays in the hub
        log — never in the user-facing response.
        """
        # If we've already decided a placement, workers are loading.
        # Otherwise the pool either has no workers or can't fit the
        # model — both look the same to the user: "need more machines".
        deployment = self._deployment_for(model_id)
        state = "loading" if deployment.current_placement else "waiting"
        return {"kind": "not_ready", "state": state}

    async def reset_conversation_on_workers(
        self,
        conv_id: str,
        model_id: str | None = None,
    ) -> None:
        """Broadcast `reset_conversation` to every compute worker. Used by
        both the chat-client /reset path and the HTTP API after each
        stateless request, so workers' per-conv KV caches don't accumulate."""
        async with self._pool_lock:
            workers = [p for p in self.participants.values() if p.compute]
        await asyncio.gather(
            *[
                w.conn.call(
                    "reset_conversation",
                    conv_id=conv_id,
                    **({"model_id": model_id} if model_id else {}),
                )
                for w in workers
            ],
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
        model_id: str | None = None,
    ) -> tuple[str, dict]:
        """Run one chat turn against `conv.history` via delegation.

        The hub no longer does per-token compute. It picks a compute
        worker via the scheduler (cheap mode: self-utilization wins when
        a querier-owned worker exists; otherwise the highest-TPS worker
        wins via the secondary tiebreak), sends `delegate_turn`, and
        forwards the worker's `delegate_token` notifications to the chat
        client (via the `on_token` callback registered for this conv).
        """
        if not isinstance(model_id, str) or not model_id:
            raise RuntimeError("model_id is required")
        selected_model = model_id
        await self.ensure_model_started(selected_model)
        if is_alive is not None and not is_alive():
            raise RuntimeError("requester disconnected")
        deployment = self._deployment_for(selected_model)
        await deployment.pool_ready.wait()
        if is_alive is not None and not is_alive():
            raise RuntimeError("requester disconnected")

        async with self._model_turn_lock:
            self._activate_model(selected_model)
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

        # Diagnostic: print the chain we're about to drive so an
        # observer can tell which workers are involved. Multi-worker
        # turns failing mid-flight is otherwise invisible — the hub
        # just hangs on `delegate_turn`.
        head_segment = f"{worker.short_id}({worker.display_owner})[0:{my_layer_end}]"
        chain_desc_parts = [head_segment]
        for hop in chain_payload:
            hid = hop["worker_id"][:8]
            hp = self.participants.get(hop["worker_id"])
            alias = hp.display_owner if hp else "?"
            chain_desc_parts.append(
                f"{hid}({alias})[{hop['layer_start']}:{hop['layer_end']}]"
            )
        chain_desc = " → ".join(chain_desc_parts)
        ui.notice(f"turn {conv.id[:8]} · {selected_model} → {chain_desc}")
        # Track tokens-so-far per conv so the watchdog can report progress.
        self._turn_token_counts[conv.id] = 0
        try:
            t0 = time.perf_counter()
            call_payload = dict(
                model_id=selected_model,
                conv_id=conv.id,
                history=conv.history,
                max_tokens=max_new_tokens,
                temperature=temperature,
            )
            if chain_payload:
                call_payload["my_layer_end"] = my_layer_end
                call_payload["chain"] = chain_payload

            async def _delegate():
                return await worker.conn.call("delegate_turn", **call_payload)

            delegate_task = asyncio.create_task(_delegate())

            # Watchdog: every 5s the call hasn't returned, log progress.
            # If a peer in the chain is hung, this surfaces it instead of
            # leaving the operator staring at a silent prompt.
            async def _watchdog():
                while not delegate_task.done():
                    await asyncio.sleep(5.0)
                    if delegate_task.done():
                        return
                    elapsed = time.perf_counter() - t0
                    tokens_so_far = self._turn_token_counts.get(conv.id, 0)
                    ui.notice(
                        f"turn {conv.id[:8]} still in flight · "
                        f"{elapsed:.0f}s · tokens={tokens_so_far}"
                    )

            wd = asyncio.create_task(_watchdog())
            try:
                resp = await delegate_task
            finally:
                wd.cancel()
                try:
                    await wd
                except (asyncio.CancelledError, Exception):
                    pass

            wall_ms = (time.perf_counter() - t0) * 1000
            tokens_total = self._turn_token_counts.get(conv.id, 0)
            ui.notice(
                f"turn {conv.id[:8]} done in {wall_ms:.0f}ms · tokens={tokens_total}"
            )
            # delegate_token notifications dispatch as fire-and-forget
            # tasks; the response (delegate_done) may resolve before the
            # last token task has run. Yield so trailing tasks complete
            # before we tear down the router and the client misses tokens.
            await asyncio.sleep(0)
        finally:
            self._token_routers.pop(conv.id, None)
            self._turn_token_counts.pop(conv.id, None)

        if resp.get("kind") != "delegate_done":
            raise RuntimeError(resp.get("error", "delegate_turn failed"))

        # Capture the worker's reported active_params for ledger CU math.
        # The hub itself no longer loads the model, so this is the only
        # path that surfaces param count to the attribution code.
        active = int(resp.get("active_params") or 0)
        if active and not deployment.active_params:
            deployment.active_params = active

        return resp["text"], {
            "prefill_tokens": int(resp.get("prefill_tokens", 0)),
            "prefill_ms": float(resp.get("prefill_ms", 0.0)),
            "decode_tokens": int(resp.get("decode_tokens", 0)),
            "decode_ms": float(resp.get("elapsed_ms", wall_ms)),
            "worker_id": worker.id,
            "model_id": selected_model,
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
        deployment = self._deployment_for()
        candidates = []
        for participant in self.participants.values():
            state = deployment.worker_states.get(participant.id)
            if state is None:
                ready = participant.ready
                backend = participant.backend
            else:
                ready = state.ready
                backend = state.backend
            if participant.compute and ready and backend == deployment.selected_backend:
                candidates.append(participant)
        if not candidates:
            return None, 0, []

        # If the hub locked a *true* multi-segment partition placement
        # (each worker holds only its layer slice), use it directly. We
        # detect this by the head segment's end being less than
        # n_layers — otherwise it's a "full to everyone" placement and
        # the chain path doesn't apply.
        partition_active = (
            deployment.current_placement
            and len(deployment.current_placement) >= 2
            and deployment.current_placement[0][2] < deployment.n_layers
        )
        if partition_active:
            ready_ids = {p.id for p in candidates}
            if all(seg[0] in ready_ids for seg in deployment.current_placement):
                head_id, _, head_end, _ = deployment.current_placement[0]
                head = next((p for p in candidates if p.id == head_id), None)
                if head is not None:
                    chain_payload: list[dict] = []
                    for worker_id, start, end, _ in deployment.current_placement[1:]:
                        p = next((p for p in candidates if p.id == worker_id), None)
                        if (
                            p is None
                            or p.peer_listen_port is None
                            or p.peer_listen_host is None
                        ):
                            chain_payload = []
                            break
                        chain_payload.append(
                            {
                                "worker_id": worker_id,
                                "host": p.peer_host,
                                "port": int(p.peer_listen_port),
                                "layer_start": int(start),
                                "layer_end": int(end),
                            }
                        )
                    if chain_payload or len(deployment.current_placement) == 1:
                        return head, int(head_end), chain_payload

        # Single-worker fast path: only one candidate ⇒ no chain needed.
        if (
            len(candidates) == 1
            or deployment.geometry is None
            or deployment.n_layers <= 1
        ):
            head = self._pick_delegate_worker(querier_account=querier_account)
            return (
                head,
                self._delegate_layer_end(head, deployment),
                [],
            )

        # Multi-worker: ask the scheduler to place decoder-layer units.
        workers_snapshot, latency = self.pool_view.snapshot()
        ready_ids = {p.id for p in candidates}
        workers_snapshot = [w for w in workers_snapshot if w.worker_id in ready_ids]

        geom = deployment.geometry
        if geom is None:
            head = self._pick_delegate_worker(querier_account=querier_account)
            return head, self._delegate_layer_end(head, deployment), []
        # Use a small per-layer FLOP estimate so the cost model reflects
        # real work (and the scheduler tiebreaks reasonably). RAM is set
        # to 0 because every ready worker already has the full model
        # loaded in v1 — they're not constrained by additional RAM here.
        # Once partial loading lands, ram_required_gb gets the real number.
        per_layer_params = TRANSFORMER_LAYER_FLOP_PARAM_FACTOR * geom.hidden_size**2
        per_layer_flops = float(2 * per_layer_params)
        units = [
            WorkUnit(
                unit_id=f"block_{i}",
                ram_required_gb=0.0,
                flops_per_token=per_layer_flops,
            )
            for i in range(geom.n_layers)
        ]
        placement = schedule(
            units,
            workers_snapshot,
            latency,
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
            if p is None or p.peer_listen_port is None or p.peer_listen_host is None:
                # Chain peer unreachable with this placement.
                return head, geom.n_layers, []
            chain_payload.append(
                {
                    "worker_id": worker_id,
                    "host": p.peer_host,
                    "port": int(p.peer_listen_port),
                    "layer_start": start,
                    "layer_end": end,
                }
            )
        return head, head_end, chain_payload

    def _delegate_layer_end(
        self,
        participant: Participant | None,
        deployment: ModelDeployment,
    ) -> int:
        if participant is None:
            return deployment.n_layers
        state = deployment.worker_states.get(participant.id)
        layer_range = (
            state.layer_range if state is not None else participant.layer_range
        )
        return layer_range[1] if layer_range else deployment.n_layers

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
        deployment = self._deployment_for()
        candidates = []
        for participant in self.participants.values():
            state = deployment.worker_states.get(participant.id)
            if state is None:
                ready = participant.ready
                backend = participant.backend
            else:
                ready = state.ready
                backend = state.backend
            if participant.compute and ready and backend == deployment.selected_backend:
                candidates.append(participant)
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
        flops = 2 * (deployment.active_params or 1)
        unit = WorkUnit(
            unit_id="model",
            ram_required_gb=0.0,  # already loaded; don't double-count
            flops_per_token=float(flops),
        )
        placement = schedule(
            [unit],
            workers_snapshot,
            latency,
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
        model_id = msg.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            return None
        deployment = self._deployment_for(model_id)
        state = deployment.worker_states.setdefault(worker_id, ModelWorkerState())
        backend = msg.get("backend")
        if backend != deployment.selected_backend:
            log.debug(
                "ignoring worker_ready for backend %r; selected backend is %r",
                backend,
                deployment.selected_backend,
            )
            return None
        state.backend = backend if isinstance(backend, str) else None
        state.ready = True
        self._sync_legacy_participant_state(p, model_id=model_id)
        n_layers = int(msg.get("n_layers") or 0)
        if n_layers and not deployment.n_layers:
            deployment.n_layers = n_layers
        active = int(msg.get("active_params") or 0)
        if active and not deployment.active_params:
            deployment.active_params = active
        geom = msg.get("geometry")
        if isinstance(geom, dict) and deployment.geometry is None:
            try:
                deployment.geometry = ModelGeometry(
                    n_layers=int(geom["n_layers"]),
                    hidden_size=int(geom["hidden_size"]),
                    vocab_size=int(geom["vocab_size"]),
                    layer_bytes_per_param=float(geom["layer_bytes_per_param"]),
                    head_bytes_per_param=float(geom["head_bytes_per_param"]),
                    layer_ram_gb=tuple(float(x) for x in geom["layer_ram_gb"]),
                    embed_ram_gb=float(geom["embed_ram_gb"]),
                    lm_head_ram_gb=(
                        float(geom["lm_head_ram_gb"])
                        if geom.get("lm_head_ram_gb") is not None
                        else None
                    ),
                    head_extra_ram_gb=float(geom["head_extra_ram_gb"]),
                    tie_word_embeddings=bool(geom["tie_word_embeddings"]),
                    metadata_source=str(geom["metadata_source"]),
                )
            except (KeyError, TypeError, ValueError):
                log.debug("ignoring malformed geometry payload", exc_info=True)
        # Synthesize a layer_range so the ledger attribution path keeps
        # working. Each worker hosts the entire model in v1, so the range
        # is the full set.
        # `layer_range` was set in _broadcast_load_units to the actual
        # assignment (which may be a slice). Only fall back to "the
        # whole model" if no load_units was ever sent (legacy path).
        if deployment.n_layers and state.layer_range is None:
            state.layer_range = (0, deployment.n_layers)
            self._sync_legacy_participant_state(p, model_id=model_id)
        was_ready = deployment.pool_ready.is_set()
        # Only mark the pool ready when EVERY worker in the placement
        # has reported back. A single ready worker in a multi-worker
        # chain can't serve a turn alone — the chain breaks at the
        # un-loaded peer and the chat prompt hangs forever.
        self._refresh_pool_ready(model_id=model_id)
        a, b = state.layer_range or (0, deployment.n_layers)
        ui.notice(
            f"worker·{p.short_id} ({p.display_owner}) ready for "
            f"{model_id} (layers [{a}:{b}])"
        )
        # Log a single "pool ready" line once every assigned worker has
        # reported back, so the operator knows queries will now succeed.
        placement_ids = {seg[0] for seg in deployment.current_placement}
        if placement_ids and deployment.pool_ready.is_set() and not was_ready:
            ui.notice(f"model ready — accepting queries for {model_id}")
        # Now that this worker can serve, kick off pairwise RTT probes
        # between it and every other ready compute worker. Each pair
        # produces one cell in `pool_view.latency`.
        if (
            p.compute
            and p.peer_listen_port is not None
            and p.peer_listen_host is not None
        ):
            asyncio.create_task(self._probe_pairs_for(worker_id))
        return None

    async def _probe_pairs_for(self, worker_id: str) -> None:
        """For each existing ready compute worker, ask both sides to
        ping each other and record the median RTT into pool_view.

        Probes are fired concurrently (both directions per pair) but
        the mesh log is emitted only after they all complete — printing
        once per probe spammed N*(N-1) almost-identical tables per
        worker join.
        """
        async with self._pool_lock:
            me = self.participants.get(worker_id)
            if (
                me is None
                or not me.compute
                or not me.ready
                or me.peer_listen_host is None
                or me.peer_listen_port is None
            ):
                return
            others = [
                p
                for p in self.participants.values()
                if (
                    p.id != worker_id
                    and p.compute
                    and p.ready
                    and p.peer_listen_port is not None
                    and p.peer_listen_host is not None
                )
            ]
        if not others:
            return
        tasks = []
        for other in others:
            tasks.append(
                self._probe_one(
                    me.id, other.id, other.peer_host, other.peer_listen_port
                )
            )
            tasks.append(
                self._probe_one(other.id, me.id, me.peer_host, me.peer_listen_port)
            )
        await asyncio.gather(*tasks, return_exceptions=True)
        # Single mesh print once every probe in this batch settled.
        self._log_latency_mesh()

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
            log.debug(
                "probe call failed (%s → %s)", source_id, target_id, exc_info=True
            )
            return
        if resp.get("kind") != "probe_result" or "error" in resp:
            return
        try:
            rtt_p50 = float(resp["rtt_p50_ms"])
            self.pool_view.record_probe(
                source_id,
                target_id,
                rtt_p50,
                float(resp.get("rtt_p99_ms", 0.0)),
                int(resp.get("samples", 1)),
            )
        except Exception:
            log.debug("could not record probe result", exc_info=True)

    def _log_latency_mesh(self) -> None:
        """Pretty-print the current pairwise RTT matrix between compute
        workers. Called whenever a probe updates a cell so the operator
        can watch the mesh fill in as workers join."""
        workers = sorted(
            (
                p
                for p in self.participants.values()
                if (
                    p.compute
                    and p.peer_listen_port is not None
                    and p.peer_listen_host is not None
                )
            ),
            key=lambda p: p.id,
        )
        if len(workers) < 2:
            return
        # Column / row labels: just the display owner. The short_id is
        # in the join/leave lines already; tacking it onto every cell of
        # an NxN mesh table is noise.
        labels = [p.display_owner for p in workers]
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
        # Increment per-conv token counter so the run_turn watchdog can
        # report progress (e.g. "still in flight · 30s · tokens=12").
        if conv_id in self._turn_token_counts:
            self._turn_token_counts[conv_id] += 1
            # Log the first token's wall-clock so we can see whether
            # prefill is the slow part or decode is.
            if self._turn_token_counts[conv_id] == 1:
                ui.notice(f"turn {conv_id[:8]} first token received")
        cb = self._token_routers.get(conv_id)
        if cb is not None:
            try:
                await cb(text)
            except Exception:
                # The chat client went away mid-turn. Drop the router so
                # we stop forwarding: otherwise every remaining token of
                # the turn retries the dead socket, and asyncio logs a
                # "socket.send() raised exception" warning per token,
                # flooding the hub until the turn (or the operator) ends.
                self._token_routers.pop(conv_id, None)
                log.debug(
                    "token forward failed; client gone, dropping router for %s",
                    conv_id[:8],
                    exc_info=True,
                )
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
        if self._selected_backend is not None:
            workers = [w for w in workers if self._selected_backend in w.backends]
        if not workers:
            return None
        return schedule(
            units,
            workers,
            latency,
            querier_account=querier_account,
            mode=mode,
            max_segments=max_segments,
        )

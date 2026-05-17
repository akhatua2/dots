"""Hub: pure orchestrator. The pool is the set of connected workers.

Every connected worker is also a chatter — they joined to use the model AND
to contribute their compute. The hub:

  • Loads the model locally for tokenizer + embed + norm + lm_head + sampling.
    (No transformer layers are executed on the hub.)
  • Accepts worker TCP connections. After HELLO with their free_ram_gb, the
    worker is added to the pool, layers are re-planned across all current
    participants, and LOAD_SHARD is broadcast.
  • Maintains one Conversation per worker — their personal chat state.
  • On worker PROMPT, runs a chat turn for that worker: builds new tokens
    via chat template (history-aware), prefills the new portion through the
    full pipeline, decodes, streaming each token back to the worker via a
    fire-and-forget TOKEN notification.
  • Empty pool → hub waits, can't serve. First worker joining → they run all
    layers (solo). N workers → layers split N ways.

Topology change (any join/leave) increments a generation counter. In-flight
turns check the counter at every pipeline step and abort cleanly if it
moved. All conversations' caches are reset on topology change because the
layer-→-cache mapping changes.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

import mlx.core as mx
from mlx_lm import load as mlx_load

from . import ui
from .peer import PeerConnection
from .protocol import decode_array, encode_array

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
    max_load_gb: float | None = None           # None == no cap
    layer_range: tuple[int, int] | None = None
    ready: bool = False
    conv: Conversation = field(default_factory=lambda: Conversation(id=str(uuid.uuid4())))

    @property
    def short_id(self) -> str:
        return self.id[:8] if self.id else "?"


class TopologyChanged(Exception):
    """Raised inside a turn when the pool changed under our feet."""


def plan_layers(
    weights: list[float],
    n_layers: int,
    caps: list[int] | None = None,
) -> list[tuple[int, int]] | None:
    """Split n_layers into len(weights) contiguous ranges proportional to
    weights, optionally honoring a per-worker layer cap.

    If `caps` is given, each worker's range is capped at caps[i] layers.
    If the total capacity (sum of caps) is less than n_layers, returns
    None — the model can't be distributed across this pool.
    Otherwise returns a list of (start, end) tuples covering [0, n_layers).
    """
    if not weights:
        return [] if n_layers == 0 else None
    if caps is not None and sum(caps) < n_layers:
        return None

    total = sum(weights) or 1.0
    raw = [w / total * n_layers for w in weights]
    sizes = [max(1, int(round(r))) for r in raw]
    if caps is not None:
        sizes = [min(sizes[i], caps[i]) for i in range(len(sizes))]

    # Reconcile rounding/cap drift to land on exactly n_layers.
    while sum(sizes) != n_layers:
        diff = n_layers - sum(sizes)
        if diff > 0:
            # Need more layers somewhere with spare room.
            if caps is not None:
                spare = [(caps[i] - sizes[i], i) for i in range(len(sizes))
                         if caps[i] > sizes[i]]
                if not spare:
                    return None
                _, i = max(spare)
            else:
                i = sizes.index(min(sizes))
            sizes[i] += 1
        else:
            # Too many — drop one from a worker that has more than its
            # minimum (1). Prefer the largest range so the distribution
            # stays balanced.
            candidates = [(sizes[i], i) for i in range(len(sizes))
                          if sizes[i] > 1]
            if not candidates:
                return None
            _, i = max(candidates)
            sizes[i] -= 1

    ranges: list[tuple[int, int]] = []
    cur = 0
    for s in sizes:
        ranges.append((cur, cur + s))
        cur += s
    return ranges


def _per_layer_bytes(model, n_layers: int) -> int:
    """Approximate bytes-per-layer of `model`. Returns 0 if we can't compute
    it (e.g. test fakes whose Modules don't expose .parameters)."""
    try:
        from mlx.utils import tree_flatten
        total = 0
        for layer in model.model.layers:
            params = layer.parameters() if hasattr(layer, "parameters") else {}
            for _, v in tree_flatten(params):
                if hasattr(v, "nbytes"):
                    total += v.nbytes
        return total // n_layers if n_layers > 0 else 0
    except Exception:
        log.debug("per-layer bytes unavailable", exc_info=True)
        return 0


class Hub:
    def __init__(
        self,
        port: int,
        model_id: str,
        host: str = "0.0.0.0",
        http_port: int | None = None,
    ):
        self.host = host
        self.port = port
        self.http_port = http_port
        self.model_id = model_id

        self.model = None
        self.tokenizer = None
        self.n_layers = 0
        # Approximate bytes for a single transformer block, computed once
        # after the model loads. Used to translate a worker's max_load_gb
        # into a max-layers cap when planning.
        self.bytes_per_layer = 0

        self.participants: dict[str, Participant] = {}
        self._pool_lock = asyncio.Lock()
        self._pool_ready = asyncio.Event()  # at least one ready participant
        # Set after the model is loaded AND the server is listening — useful
        # for callers (tests, embeddings) that want a deterministic ready signal.
        self.started = asyncio.Event()
        self._topology_gen = 0

    # ───────────────────────────── lifecycle ─────────────────────────────

    async def start(self) -> None:
        ui.notice(f"loading model {self.model_id} on hub…")
        self.model, self.tokenizer = await asyncio.to_thread(mlx_load, self.model_id)
        self.n_layers = len(self.model.model.layers)
        self.bytes_per_layer = await asyncio.to_thread(
            _per_layer_bytes, self.model, self.n_layers,
        )
        if self.bytes_per_layer:
            ui.notice(
                f"model ready ({self.n_layers} layers, "
                f"{self.bytes_per_layer / 1e6:.1f} MB / layer)"
            )
        else:
            ui.notice(f"model ready ({self.n_layers} layers)")

        server = await asyncio.start_server(self._on_connect, self.host, self.port)
        addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
        ui.notice(f"hub listening on {addrs} — waiting for workers to join")
        self.started.set()

        # Optional OpenAI-compatible HTTP API alongside the worker TCP server.
        # Lives only for as long as the hub does; cancellation tears it down.
        http_task: asyncio.Task | None = None
        if self.http_port is not None:
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
            participant = Participant(
                id=worker_id, addr=addr_str, free_ram_gb=free_ram,
                compute=compute, max_load_gb=max_load_gb, conn=conn,
            )
            self.participants[worker_id] = participant
        cap_str = f", cap={max_load_gb:.1f}GB" if max_load_gb is not None else ""
        role = "worker" if compute else "client"
        ui.notice(f"{role}[{participant.short_id}] joined "
                  f"({addr_str}, free_ram={free_ram:.1f}GB{cap_str})")
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
            left_role = "worker" if participant.compute else "client"
            ui.notice(f"{left_role}[{participant.short_id}] left")
            await self._on_pool_changed()

    # ─────────────────────────── re-plan on change ──────────────────────

    async def _on_pool_changed(self) -> None:
        async with self._pool_lock:
            self._topology_gen += 1
            # Only compute-contributing participants get layers assigned;
            # pure chat clients are tracked for their conversations only.
            workers = sorted(
                (p for p in self.participants.values() if p.compute),
                key=lambda w: w.id,
            )
            # Crucial: clear pool_ready *here*, not just on empty-pool. Any
            # topology change makes every worker's layer assignment stale
            # until LOAD_SHARD finishes; the retry path in _turn must wait
            # for that, otherwise _pipeline runs with zero ready workers
            # and the hub samples from raw embeddings (the same token over
            # and over — the bug the user observed as 'their their their').
            self._pool_ready.clear()
            if not workers:
                ui.notice("no compute workers — waiting")
                return

            caps = self._compute_caps(workers)
            ranges = plan_layers(
                [w.free_ram_gb for w in workers], self.n_layers, caps=caps,
            )
            if ranges is None:
                # Total pool capacity is less than the model needs. Stay
                # un-ready; the next join may close the gap.
                total_cap = sum(caps) if caps else 0
                ui.notice(
                    f"pool capacity {total_cap} layers < {self.n_layers} required"
                    f" — waiting for more workers"
                )
                for w in workers:
                    w.layer_range = None
                    w.ready = False
                return
            for w, r in zip(workers, ranges, strict=True):
                w.layer_range = r
                w.ready = False

            # Topology change invalidates every conversation's worker-side KV
            # caches — but NOT the hub-side chat history, which is the source
            # of truth we'll re-prefill from on the next compute call. Wiping
            # history here used to drop the just-appended user message of any
            # in-flight turn and left the model generating from a context
            # missing the user's question; see test_user_message_survives_
            # worker_drop.
            for w in workers:
                w.conv.token_count = 0

            workers_snapshot = list(workers)

        # Broadcast LOAD_SHARD (outside the lock).
        loads = [
            w.conn.call(
                "load_shard",
                model_id=self.model_id,
                layer_start=w.layer_range[0],
                layer_end=w.layer_range[1],
            )
            for w in workers_snapshot
        ]
        results = await asyncio.gather(*loads, return_exceptions=True)
        async with self._pool_lock:
            for w, res in zip(workers_snapshot, results, strict=True):
                if isinstance(res, Exception):
                    log.warning("w%d LOAD_SHARD failed: %s", w.id, res)
                    self.participants.pop(w.id, None)
                else:
                    w.ready = True
            ready_count = sum(1 for w in self.participants.values() if w.ready)
            if ready_count > 0:
                self._pool_ready.set()
                # Avoid `[uuid][a:b]` here — rich's markup parser would
                # try to interpret the bracketed UUID as a style tag and
                # silently swallow it. Use a colon-separated form instead.
                plan_str = "  →  ".join(
                    f"w·{w.short_id} layers {w.layer_range[0]}-{w.layer_range[1]}"
                    for w in sorted(self.participants.values(),
                                    key=lambda x: (x.layer_range or (0, 0))[0])
                    if w.ready and w.layer_range
                )
                ui.notice(f"pool ready ({ready_count}): {plan_str}")
            else:
                self._pool_ready.clear()

    def _compute_caps(self, workers: list[Participant]) -> list[int] | None:
        """Translate each worker's max_load_gb into a max-layers count.
        Returns None if we can't compute per-layer bytes (e.g. fake model
        in tests) — caller treats no caps as "no constraint"."""
        if self.bytes_per_layer <= 0:
            return None
        if not any(w.max_load_gb is not None for w in workers):
            return None
        caps: list[int] = []
        for w in workers:
            if w.max_load_gb is None:
                caps.append(self.n_layers)            # effectively uncapped
            else:
                max_l = max(1, int(w.max_load_gb * 1e9 // self.bytes_per_layer))
                caps.append(min(max_l, self.n_layers))
        return caps

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
        return {"kind": "turn_done", **stats}

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
    ) -> tuple[str, dict]:
        """Run one chat turn against `conv.history`.

        The history must already end with the user message to answer.
        Returns (response_text, stats_dict).

        Does NOT mutate `conv.history` — the caller decides whether to
        keep the response (chat-client path appends it; HTTP API throws
        the whole conversation away after the response is delivered).

        Survives worker drops mid-turn: if a `_pipeline` call raises
        `ConnectionError` or `TopologyChanged`, the retry re-prefills the
        full history + everything-we've-already-streamed-to-the-user on
        the new pool. The user sees a pause, not a truncated response.

        on_token, if given, is awaited for each streamed text chunk.
        is_alive, if given, is checked at the top of every retry; when
        it returns False we abort because the requester went away.
        """
        # State that lives across recovery attempts:
        response_tokens: list[int] = []
        stop_ids = self._stop_token_ids()
        first_prefill_tokens = 0
        first_prefill_ms = 0.0
        decode_start_ts: float | None = None
        attempt = 0

        full_ids = self.tokenizer.apply_chat_template(
            conv.history, add_generation_prompt=True
        )

        while True:
            try:
                if is_alive is not None and not is_alive():
                    raise RuntimeError("requester disconnected")
                await self._pool_ready.wait()
                my_gen = self._topology_gen

                target_seq = full_ids + response_tokens
                if conv.token_count > len(target_seq):
                    conv.token_count = 0
                prefill_ids = target_seq[conv.token_count:]

                if prefill_ids:
                    t0 = time.perf_counter()
                    prompt_ids = mx.array([prefill_ids])
                    hidden = self.model.model.embed_tokens(prompt_ids)
                    hidden = await self._pipeline(conv.id, hidden, my_gen)
                    logits = self._head(hidden[:, -1:, :])
                    token = self._sample(logits[0, -1], temperature)
                    conv.token_count = len(target_seq)
                    if attempt == 0:
                        first_prefill_tokens = len(prefill_ids)
                        first_prefill_ms = (time.perf_counter() - t0) * 1000
                else:
                    raise RuntimeError(
                        "no tokens to prefill — caches are inconsistent"
                    )

                if decode_start_ts is None:
                    decode_start_ts = time.perf_counter()

                while (len(response_tokens) < max_new_tokens
                       and token not in stop_ids):
                    text = self.tokenizer.decode([token])
                    if on_token is not None:
                        await on_token(text)
                    response_tokens.append(token)
                    next_in = mx.array([[token]])
                    next_hidden = self.model.model.embed_tokens(next_in)
                    hidden = await self._pipeline(conv.id, next_hidden, my_gen)
                    conv.token_count += 1
                    logits = self._head(hidden[:, -1:, :])
                    token = self._sample(logits[0, -1], temperature)

                terminator = (token if token in stop_ids
                              else self._primary_eos())
                term_in = mx.array([[terminator]])
                term_hidden = self.model.model.embed_tokens(term_in)
                await self._pipeline(conv.id, term_hidden, my_gen)
                response_tokens.append(terminator)
                conv.token_count += 1
                break

            except (ConnectionError, TopologyChanged) as e:
                attempt += 1
                ui.notice(
                    f"turn interrupted (attempt {attempt}, {e}); reconciling…"
                )
                conv.token_count = 0
                await asyncio.sleep(0.1)
                await self._pool_ready.wait()

        elapsed_ms = ((time.perf_counter() - decode_start_ts) * 1000
                      if decode_start_ts else 0.0)
        # response_tokens includes the trailing terminator (so the cache
        # ends the turn cleanly), but we don't want it in the user-facing
        # text — strip the last token.
        response_text = self.tokenizer.decode(response_tokens[:-1])
        return response_text, {
            "prefill_tokens": first_prefill_tokens,
            "prefill_ms": first_prefill_ms,
            "decode_tokens": len(response_tokens) - 1,
            "decode_ms": elapsed_ms,
            "recoveries": attempt,
        }

    async def _pipeline(self, conv_id: str, hidden: mx.array, my_gen: int) -> mx.array:
        if my_gen != self._topology_gen:
            raise TopologyChanged()
        async with self._pool_lock:
            ordered = sorted(
                (w for w in self.participants.values() if w.ready and w.layer_range),
                key=lambda w: w.layer_range[0],
            )
        for w in ordered:
            if my_gen != self._topology_gen:
                raise TopologyChanged()
            resp = await w.conn.call(
                "compute", conv_id=conv_id, hidden=encode_array(hidden),
            )
            if resp.get("kind") != "compute_out":
                raise RuntimeError(f"w{w.id} compute failed: {resp}")
            hidden = decode_array(resp["hidden"])
        return hidden

    # ──────────────────────────── helpers ───────────────────────────────

    def _head(self, hidden: mx.array) -> mx.array:
        normed = self.model.model.norm(hidden)
        if self.model.args.tie_word_embeddings:
            return self.model.model.embed_tokens.as_linear(normed)
        return self.model.lm_head(normed)

    @staticmethod
    def _sample(logits_row: mx.array, temperature: float) -> int:
        if temperature <= 0:
            return int(mx.argmax(logits_row, axis=-1).item())
        return int(mx.random.categorical(logits_row / temperature).item())

    def _primary_eos(self) -> int:
        if self.tokenizer.eos_token_id is not None:
            return int(self.tokenizer.eos_token_id)
        for marker in ("<|im_end|>", "<|endoftext|>", "<|eot_id|>"):
            try:
                tid = self.tokenizer.convert_tokens_to_ids(marker)
                if isinstance(tid, int) and tid > 0:
                    return tid
            except Exception:
                continue
        return 0

    def _stop_token_ids(self) -> set[int]:
        ids: set[int] = set()
        if self.tokenizer.eos_token_id is not None:
            ids.add(int(self.tokenizer.eos_token_id))
        for marker in ("<|im_end|>", "<|endoftext|>", "<|eot_id|>"):
            try:
                tid = self.tokenizer.convert_tokens_to_ids(marker)
            except Exception:
                continue
            if isinstance(tid, int) and tid > 0:
                ids.add(tid)
        return ids

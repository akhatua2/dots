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
    id: int
    addr: str
    free_ram_gb: float
    conn: PeerConnection
    layer_range: tuple[int, int] | None = None
    ready: bool = False
    conv: Conversation = field(default_factory=lambda: Conversation(id=str(uuid.uuid4())))


class TopologyChanged(Exception):
    """Raised inside a turn when the pool changed under our feet."""


def plan_layers(weights: list[float], n_layers: int) -> list[tuple[int, int]]:
    """Split n_layers into len(weights) contiguous ranges, proportional to
    weights. Each range gets at least 1 layer; rounding drift goes to the last."""
    if not weights:
        return []
    total = sum(weights) or 1.0
    raw = [w / total * n_layers for w in weights]
    sizes = [max(1, int(round(r))) for r in raw]
    sizes[-1] += n_layers - sum(sizes)
    if sizes[-1] < 1:
        # Too many participants for too few layers; redistribute by stealing
        # from the largest range.
        while sizes[-1] < 1:
            donor = sizes.index(max(sizes))
            if sizes[donor] <= 1:
                break
            sizes[donor] -= 1
            sizes[-1] += 1
    ranges: list[tuple[int, int]] = []
    cur = 0
    for s in sizes:
        ranges.append((cur, cur + s))
        cur += s
    return ranges


class Hub:
    def __init__(self, port: int, model_id: str, host: str = "0.0.0.0"):
        self.host = host
        self.port = port
        self.model_id = model_id

        self.model = None
        self.tokenizer = None
        self.n_layers = 0

        self.participants: dict[int, Participant] = {}
        self._next_id = 1
        self._pool_lock = asyncio.Lock()
        self._pool_ready = asyncio.Event()  # at least one ready participant
        self._topology_gen = 0

    # ───────────────────────────── lifecycle ─────────────────────────────

    async def start(self) -> None:
        ui.info(f"loading model {self.model_id} on hub…")
        self.model, self.tokenizer = await asyncio.to_thread(mlx_load, self.model_id)
        self.n_layers = len(self.model.model.layers)
        ui.info(f"model ready ({self.n_layers} layers)")

        server = await asyncio.start_server(self._on_connect, self.host, self.port)
        addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
        ui.info(f"hub listening on {addrs} — waiting for workers to join")
        async with server:
            await server.serve_forever()

    # ─────────────────────────── connection lifecycle ────────────────────

    async def _on_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer_addr = writer.get_extra_info("peername")
        addr_str = f"{peer_addr[0]}:{peer_addr[1]}"
        worker_id = self._next_id
        self._next_id += 1

        hello_fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()

        async def handler(msg: dict) -> dict | None:
            kind = msg.get("kind")
            if kind == "hello":
                if not hello_fut.done():
                    hello_fut.set_result(msg)
                return {"kind": "hello_ack", "n_layers": self.n_layers,
                        "model_id": self.model_id}
            if kind == "prompt":
                return await self._on_prompt(worker_id, msg)
            if kind == "reset":
                return await self._on_reset(worker_id)
            return {"kind": "error",
                    "error": f"unexpected worker-initiated kind: {kind}"}

        conn = PeerConnection(reader, writer, addr_str, handler)
        log.info("worker %d connected from %s", worker_id, addr_str)

        try:
            hello_msg = await asyncio.wait_for(hello_fut, timeout=HELLO_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            log.warning("worker %d: HELLO timeout, dropping", worker_id)
            await conn.close()
            return

        free_ram = float(hello_msg.get("free_ram_gb", 0.0))
        participant = Participant(
            id=worker_id, addr=addr_str, free_ram_gb=free_ram, conn=conn,
        )
        async with self._pool_lock:
            self.participants[worker_id] = participant
        ui.info(f"w{worker_id} joined ({addr_str}, free_ram={free_ram:.1f}GB)")
        await self._on_pool_changed()
        # Tell this worker which conv_id we assigned them, so the chat REPL
        # knows what to send back.
        try:
            await conn.notify("welcome", conv_id=participant.conv.id)
        except Exception:
            pass

        try:
            await conn._closed.wait()
        finally:
            async with self._pool_lock:
                self.participants.pop(worker_id, None)
            ui.info(f"w{worker_id} left")
            await self._on_pool_changed()

    # ─────────────────────────── re-plan on change ──────────────────────

    async def _on_pool_changed(self) -> None:
        async with self._pool_lock:
            self._topology_gen += 1
            workers = sorted(self.participants.values(), key=lambda w: w.id)
            if not workers:
                self._pool_ready.clear()
                ui.info("pool empty")
                return

            ranges = plan_layers([w.free_ram_gb for w in workers], self.n_layers)
            for w, r in zip(workers, ranges, strict=True):
                w.layer_range = r
                w.ready = False

            # Topology change invalidates every conversation's KV caches.
            for w in workers:
                w.conv.history.clear()
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
                plan_str = " → ".join(
                    f"w{w.id}[{w.layer_range[0]}:{w.layer_range[1]}]"
                    for w in sorted(self.participants.values(),
                                    key=lambda x: x.layer_range[0])
                    if w.ready and w.layer_range
                )
                ui.info(f"pool ready ({ready_count}): {plan_str}")
            else:
                self._pool_ready.clear()

        # Tell every worker their conversation reset (so their REPL UI can
        # reflect it, e.g. clear scrollback).
        async with self._pool_lock:
            workers_now = list(self.participants.values())
        for w in workers_now:
            try:
                await w.conn.notify("conversation_reset", reason="topology_changed")
            except Exception:
                pass

    # ────────────────────────────── one turn ────────────────────────────

    async def _on_prompt(self, worker_id: int, msg: dict) -> dict:
        async with self._pool_lock:
            participant = self.participants.get(worker_id)
        if participant is None:
            return {"kind": "error", "error": "worker no longer in pool"}

        if not self._pool_ready.is_set():
            return {"kind": "error", "error": "pool not ready"}

        try:
            stats = await self._turn(
                participant,
                msg["text"],
                max_new_tokens=int(msg.get("max_tokens", DEFAULT_MAX_NEW_TOKENS)),
                temperature=float(msg.get("temperature", DEFAULT_TEMPERATURE)),
            )
            return {"kind": "turn_done", **stats}
        except TopologyChanged:
            return {"kind": "error", "error": "topology changed during turn"}
        except ConnectionError as e:
            return {"kind": "error", "error": f"worker dropped mid-turn: {e}"}
        except Exception as e:
            log.exception("turn failed for w%d", worker_id)
            return {"kind": "error", "error": str(e)}

    async def _on_reset(self, worker_id: int) -> dict:
        async with self._pool_lock:
            participant = self.participants.get(worker_id)
            workers = list(self.participants.values())
        if participant is None:
            return {"kind": "error", "error": "worker no longer in pool"}
        conv_id = participant.conv.id
        await asyncio.gather(
            *[w.conn.call("reset_conversation", conv_id=conv_id) for w in workers],
            return_exceptions=True,
        )
        participant.conv.history.clear()
        participant.conv.token_count = 0
        return {"kind": "ok"}

    async def _turn(self, p: Participant, user_msg: str,
                    max_new_tokens: int, temperature: float) -> dict:
        my_gen = self._topology_gen
        conv = p.conv

        conv.history.append({"role": "user", "content": user_msg})
        try:
            full_ids = self.tokenizer.apply_chat_template(
                conv.history, add_generation_prompt=True
            )
            new_ids = full_ids[conv.token_count:]
            if not new_ids:
                conv.history.pop()
                return {"prefill": 0, "decode": 0, "elapsed_ms": 0}

            stop_ids = self._stop_token_ids()

            # Prefill
            prompt_ids = mx.array([new_ids])
            t0 = time.perf_counter()
            hidden = self.model.model.embed_tokens(prompt_ids)
            hidden = await self._pipeline(conv.id, hidden, my_gen)
            logits = self._head(hidden[:, -1:, :])
            token = self._sample(logits[0, -1], temperature)
            prefill_ms = (time.perf_counter() - t0) * 1000

            # Decode + stream
            decode_start = time.perf_counter()
            response_tokens: list[int] = []
            n_decoded = 0
            while n_decoded < max_new_tokens and token not in stop_ids:
                text = self.tokenizer.decode([token])
                await p.conn.notify("token", text=text)
                response_tokens.append(token)
                n_decoded += 1
                next_in = mx.array([[token]])
                next_hidden = self.model.model.embed_tokens(next_in)
                hidden = await self._pipeline(conv.id, next_hidden, my_gen)
                logits = self._head(hidden[:, -1:, :])
                token = self._sample(logits[0, -1], temperature)

            # Always push a terminator so caches end the turn cleanly.
            terminator = token if token in stop_ids else self._primary_eos()
            term_in = mx.array([[terminator]])
            term_hidden = self.model.model.embed_tokens(term_in)
            await self._pipeline(conv.id, term_hidden, my_gen)
            response_tokens.append(terminator)

            elapsed_ms = (time.perf_counter() - decode_start) * 1000
            response_text = self.tokenizer.decode(response_tokens)
            conv.history.append({"role": "assistant", "content": response_text})
            conv.token_count = len(full_ids) + len(response_tokens)
            return {
                "prefill_tokens": len(new_ids), "prefill_ms": prefill_ms,
                "decode_tokens": n_decoded, "decode_ms": elapsed_ms,
            }
        except Exception:
            # Roll back the speculative history append on any failure so the
            # user can retry without the abandoned message in their context.
            if conv.history and conv.history[-1].get("role") == "user":
                conv.history.pop()
            raise

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

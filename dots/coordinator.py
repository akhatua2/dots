"""Coordinator: connects to workers, plans shard split, runs the chat loop.

The local node is also a worker — it owns the first layer range plus the
embedding, final norm, lm_head, tokenizer, and sampling. Remote workers
own contiguous middle/tail ranges.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass

import mlx.core as mx
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style

from . import ui
from .protocol import decode_array, encode_array, read_msg, write_msg
from .shard import Shard, ShardSpec
from .worker import _free_ram_gb

log = logging.getLogger("dots.coordinator")

_PROMPT_STYLE = Style.from_dict({"prompt": "ansimagenta bold", "": "ansiwhite"})


@dataclass
class RemoteWorker:
    host: str
    port: int
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    free_ram_gb: float


async def _connect(host: str, port: int) -> RemoteWorker:
    reader, writer = await asyncio.open_connection(host, port)
    await write_msg(writer, {"kind": "hello"})
    ack = await read_msg(reader)
    if ack.get("kind") != "hello_ack":
        raise RuntimeError(f"bad handshake from {host}:{port}: {ack}")
    log.info("connected to %s:%d (free_ram=%.1fGB)", host, port, ack["free_ram_gb"])
    return RemoteWorker(host, port, reader, writer, ack["free_ram_gb"])


def _plan_shards(
    n_layers: int, local_ram_gb: float, remotes: list[RemoteWorker]
) -> list[tuple[int, int]]:
    """Split layer range across (local + remotes) proportional to free RAM.

    Returns list of (start, end) ranges, with the first range belonging to local.
    """
    weights = [local_ram_gb] + [r.free_ram_gb for r in remotes]
    total = sum(weights)
    raw = [w / total * n_layers for w in weights]
    # Round to integers while preserving total = n_layers.
    sizes = [max(1, int(round(r))) for r in raw]
    drift = n_layers - sum(sizes)
    sizes[-1] += drift  # absorb rounding into the last shard
    ranges = []
    cur = 0
    for s in sizes:
        ranges.append((cur, cur + s))
        cur += s
    return ranges


class DotsCluster:
    def __init__(
        self,
        model_id: str,
        remotes: list[RemoteWorker],
        local_shard: Shard,
        remote_ranges: list[tuple[int, int]],
    ):
        self.model_id = model_id
        self.remotes = remotes
        self.local = local_shard
        self.remote_ranges = remote_ranges  # parallel to self.remotes

    @classmethod
    async def assemble(cls, model_id: str, worker_addrs: list[tuple[str, int]]):
        # 1. Connect + handshake with every remote worker.
        remotes = await asyncio.gather(*[_connect(h, p) for h, p in worker_addrs])

        # 2. We need to know the model's total layer count to plan. Load it locally
        #    first; the worker LOAD_SHARD calls happen after we know the plan.
        local_ram_gb = _free_ram_gb()
        log.info("local free_ram=%.1fGB", local_ram_gb)
        log.info("loading model locally to discover layer count: %s", model_id)
        # Temporarily load with full range; we'll re-slice once we know the plan.
        probe = await asyncio.to_thread(
            Shard,
            ShardSpec(model_id=model_id, layer_start=0, layer_end=1,
                      is_first=True, is_last=False),
        )
        n_layers = probe.n_layers
        log.info("model has %d layers", n_layers)

        # 3. Plan shard split.
        ranges = _plan_shards(n_layers, local_ram_gb, remotes)
        log.info("shard plan: %s", ranges)

        # 4. Tell each remote worker its slice. The last remote is is_last.
        for remote, (start, end) in zip(remotes, ranges[1:], strict=True):
            is_last = (end == n_layers)
            await write_msg(remote.writer, {
                "kind": "load_shard",
                "model_id": model_id,
                "layer_start": start,
                "layer_end": end,
                "is_first": False,
                "is_last": is_last,
            })
        ready_msgs = await asyncio.gather(
            *[read_msg(r.reader) for r in remotes]
        )
        for msg in ready_msgs:
            if msg.get("kind") != "ready":
                raise RuntimeError(f"worker load failed: {msg}")
        log.info("all remote workers ready")

        # 5. Build the real local shard with our final range.
        local_start, local_end = ranges[0]
        local_is_last = (local_end == n_layers) and not remotes
        local_shard = await asyncio.to_thread(
            Shard,
            ShardSpec(model_id=model_id, layer_start=local_start, layer_end=local_end,
                      is_first=True, is_last=local_is_last),
        )
        # We can drop the probe; mlx-lm caches weights so reloading is fast.
        del probe

        return cls(model_id, remotes, local_shard, list(ranges[1:]))

    async def _run_pipeline(self, tokens: mx.array) -> mx.array:
        """Run one forward pass through the full pipeline. Returns final hidden."""
        hidden = self.local.embed(tokens)
        hidden = self.local.run_layers(hidden)
        for remote in self.remotes:
            await write_msg(remote.writer, {
                "kind": "forward",
                "hidden": encode_array(hidden),
            })
            resp = await read_msg(remote.reader)
            if resp.get("kind") != "forward_out":
                raise RuntimeError(f"remote forward failed: {resp}")
            hidden = decode_array(resp["hidden"])
        return hidden

    async def _reset_kv_all(self) -> None:
        self.local.reset_kv()
        for r in self.remotes:
            await write_msg(r.writer, {"kind": "reset_kv"})
            await read_msg(r.reader)  # consume "ok"

    def _stop_token_ids(self) -> set[int]:
        """Collect every token id we treat as end-of-turn.

        Some chat-tuned models (SmolLM2, Qwen, Llama-3) end an assistant turn
        with a special token (e.g. <|im_end|>) that isn't the tokenizer's
        eos_token_id, so checking eos alone lets the loop run away.
        """
        tokenizer = self.local.tokenizer
        ids: set[int] = set()
        if tokenizer.eos_token_id is not None:
            ids.add(tokenizer.eos_token_id)
        for marker in ("<|im_end|>", "<|endoftext|>", "<|eot_id|>"):
            try:
                tid = tokenizer.convert_tokens_to_ids(marker)
            except Exception:
                continue
            if isinstance(tid, int) and tid > 0:
                ids.add(tid)
        return ids

    async def generate(self, prompt: str, max_new_tokens: int = 128) -> None:
        """Stream generation to stdout. Greedy sampling for the PoC."""
        tokenizer = self.local.tokenizer
        # Use the chat template if the tokenizer has one.
        if hasattr(tokenizer, "apply_chat_template"):
            tokens_list = tokenizer.apply_chat_template(
                [{"role": "user", "content": prompt}],
                add_generation_prompt=True,
            )
        else:
            tokens_list = tokenizer.encode(prompt)

        stop_ids = self._stop_token_ids()
        await self._reset_kv_all()

        # Prefill: one shot through the pipeline with the whole prompt.
        prompt_ids = mx.array([tokens_list])
        t0 = time.perf_counter()
        with ui.console.status("[dim]thinking…[/dim]", spinner="dots"):
            hidden = await self._run_pipeline(prompt_ids)
            logits = self.local.head(hidden[:, -1:, :])
            token = int(mx.argmax(logits, axis=-1).item())
        prefill_ms = (time.perf_counter() - t0) * 1000

        # Decode loop: plain stdout so rich doesn't try to parse token text as markup.
        decode_start = time.perf_counter()
        n_decoded = 0
        while n_decoded < max_new_tokens and token not in stop_ids:
            text = tokenizer.decode([token])
            sys.stdout.write(text)
            sys.stdout.flush()
            n_decoded += 1
            next_in = mx.array([[token]])
            hidden = await self._run_pipeline(next_in)
            logits = self.local.head(hidden[:, -1:, :])
            token = int(mx.argmax(logits, axis=-1).item())

        elapsed = time.perf_counter() - decode_start
        ui.stats(len(tokens_list), prefill_ms, n_decoded, elapsed)

    async def close(self) -> None:
        for r in self.remotes:
            r.writer.close()
            try:
                await r.writer.wait_closed()
            except Exception:
                pass


async def chat(
    model_id: str,
    worker_addrs: list[tuple[str, int]],
    one_shot: str | None = None,
    max_tokens: int = 128,
):
    with ui.console.status("[dim]connecting to workers…[/dim]", spinner="dots"):
        cluster = await DotsCluster.assemble(model_id, worker_addrs)

    pipeline_desc = [
        f"local[{cluster.local.spec.layer_start}:{cluster.local.spec.layer_end}]"
    ]
    for remote, (start, end) in zip(cluster.remotes, cluster.remote_ranges, strict=True):
        pipeline_desc.append(f"{remote.host}:{remote.port}[{start}:{end}]")
    ui.banner(model_id, pipeline_desc)

    if one_shot is not None:
        try:
            await cluster.generate(one_shot, max_new_tokens=max_tokens)
            ui.console.print()
        finally:
            await cluster.close()
        return

    session = PromptSession()
    try:
        while True:
            try:
                prompt = await session.prompt_async(
                    HTML("<prompt>❯ </prompt>"), style=_PROMPT_STYLE
                )
            except (EOFError, KeyboardInterrupt):
                ui.console.print()
                break
            prompt = prompt.strip()
            if not prompt:
                continue
            await cluster.generate(prompt, max_new_tokens=max_tokens)
            ui.console.print()
    finally:
        await cluster.close()
        ui.info("bye")

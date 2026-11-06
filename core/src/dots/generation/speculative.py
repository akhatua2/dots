"""Speculative decoding: a small draft model proposes, the pool verifies several tokens in one pass.

Across a pool of Macs every pass of the big model costs a network round trip per hop, so decoding is
bound by latency, not by the GPUs. A draft model on the head Mac proposes `k` tokens locally, where
they cost no network at all. The pool then runs the current token and the `k` proposals through every
layer in a single pass, one round trip for all of them, and keeps the proposals for as long as they
match what the big model itself samples.

Verification is by sampling: at each position the big model samples a token, and a proposal is kept
only when it is that token. Every token we emit is therefore a sample from the big model given the
tokens before it, at any temperature; the draft only decides how many we get per round trip.

Both caches end each round holding exactly the emitted tokens (rejected proposals are trimmed), so a
multi-turn conversation's next prefill starts from the right place, just as with one-token decoding.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .autoregressive import AutoregressiveGeneration
from .base import GenerationRequest, GenerationResult, GenerationStrategy

if TYPE_CHECKING:
    from ..backends.base import BackendRuntime


class SpeculativeGeneration(GenerationStrategy):
    name = "speculative"

    def __init__(self, draft: "BackendRuntime", draft_tokens: int = 4) -> None:
        if draft_tokens < 1:
            raise ValueError("draft_tokens must be at least 1")
        self.draft = draft
        self.k = draft_tokens

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        target, draft = request.runtime, self.draft
        if not (target.supports_speculation and draft.supports_speculation):
            return await AutoregressiveGeneration().generate(request)

        conv = request.conv_id
        prefill = await target.prefill(conv, request.history, forward_hidden=request.forward_hidden)
        if prefill.logits_row is None:
            return GenerationResult(text="", stats={"prefill_tokens": 0, "decode_tokens": 0,
                                                    "prefill_ms": 0.0, "elapsed_ms": 0.0})
        await draft.prefill(conv, request.history)

        out: list[int] = []

        def sample(runtime: "BackendRuntime", logits_row, before: list[int]) -> int:
            ctx = request.repetition_context
            recent = before[-ctx:] if ctx > 0 else before
            return runtime.sample_token(logits_row, request.temperature, recent_tokens=recent,
                                        repetition_penalty=request.repetition_penalty)

        token = sample(target, prefill.logits_row, out)
        stop_ids = target.stop_ids()
        detok = target.new_detokenizer()
        rounds = proposed = accepted_total = 0
        decode_start = time.perf_counter()

        async def emit(t: int) -> None:
            text = detok.add_token(t)
            if request.on_token is not None and text:
                await request.on_token(text)
            out.append(t)

        while len(out) < request.max_tokens and token not in stop_ids:
            rounds += 1
            # The draft proposes k tokens after `token`, feeding `token` and its own proposals as it goes.
            proposal: list[int] = []
            fed = token
            for _ in range(self.k):
                proposal.append(sample(draft, await draft.decode_one(conv, fed), out + [token] + proposal))
                fed = proposal[-1]
            proposed += self.k

            # One pass of the big model over `token` and every proposal: row i predicts what follows input i.
            rows = await target.decode_many(conv, [token, *proposal], forward_hidden=request.forward_hidden)
            matched: list[int] = []
            next_token = None
            for i, guess in enumerate(proposal):
                actual = sample(target, rows[i], out + [token] + matched)
                if actual != guess:
                    next_token = actual
                    break
                matched.append(guess)
            if next_token is None:  # every proposal matched: the last row gives one more token for free
                next_token = sample(target, rows[self.k], out + [token] + matched)

            # Emit `token`, then the matched proposals, until a stop token or the length limit.
            await emit(token)
            emitted, stopped = 1, False
            for guess in matched:
                if guess in stop_ids or len(out) >= request.max_tokens:
                    stopped = True
                    break
                await emit(guess)
                emitted += 1
            accepted_total += emitted - 1

            # Keep exactly what was emitted. The big model was fed 1 + k tokens; the draft was fed k.
            await _trim(target, request, conv, (1 + self.k) - emitted)
            if emitted <= self.k:
                draft.trim(conv, self.k - emitted)
            else:  # all k proposals kept: the draft never fed its last one, so it catches up
                await draft.decode_one(conv, proposal[-1])

            if stopped:
                break
            token = next_token

        tail = detok.finalize()
        if request.on_token is not None and tail:
            await request.on_token(tail)
        draft.reset_conversation(conv)
        elapsed_ms = (time.perf_counter() - decode_start) * 1000
        return GenerationResult(
            text=target.decode_tokens(out),
            stats={
                "prefill_tokens": prefill.prefill_tokens,
                "decode_tokens": len(out),
                "prefill_ms": prefill.prefill_ms,
                "elapsed_ms": elapsed_ms,
                "verify_passes": rounds,
                "draft_proposed": proposed,
                "draft_accepted": accepted_total,
            },
        )


async def _trim(target: "BackendRuntime", request: GenerationRequest, conv: str, n: int) -> None:
    if n <= 0:
        return
    target.trim(conv, n)
    if request.trim_remote is not None:
        await request.trim_remote(n)

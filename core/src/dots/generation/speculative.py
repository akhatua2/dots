"""Speculative decoding: something cheap guesses, the pool verifies several tokens in one pass.

Across a pool of Macs every pass of the big model costs a network round trip per hop, so decoding is
bound by latency, not by the GPUs. A draft on the head Mac proposes `k` tokens locally, where they cost
no network at all: the words that followed the last few earlier in the conversation, a small model with
the same tokenizer, or the big model's own draft head (`backends/mlx/mtp.py`). The pool then runs the
current token and the `k` proposals through every layer in a single pass, one round trip for all of
them, and keeps the proposals for as long as they match what the big model itself samples.

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
    from ..backends.base import BackendRuntime, PrefillOutput


class Draft:
    """What guesses ahead for a turn: a small model of its own, or the big model's own draft head."""

    # How many of the prompt's last hidden states the big model should keep for it at prefill
    context_rows = 1
    # The most it guesses before a check, unless DOTS_DRAFT_TOKENS says otherwise
    most_guesses = 4

    @property
    def ready(self) -> bool:
        return True

    async def start(self, conv_id: str, request: GenerationRequest, prefill: "PrefillOutput") -> None:
        """The big model has read the prompt."""

    async def propose(self, conv_id: str, token: int, k: int, recent: list[int] = (),
                      penalty: float = 1.0) -> list[int]:
        """Its k guesses for what follows `token`, greedy, with the big model's repetition `penalty` on the
        `recent` tokens (`token` among them) and on its own guesses."""
        raise NotImplementedError

    async def follow(self, conv_id: str, emitted: list[int], proposal: list[int], proposed: bool) -> None:
        """A check kept `emitted` (the round's token, then the guesses that held). `proposed` is False when the
        guesses were looked up instead and this draft sat the round out."""

    def finish(self, conv_id: str) -> None:
        """The turn is over."""


class ModelDraft(Draft):
    """A small model with the big one's tokenizer, run whole on the head Mac, with a cache of its own."""

    def __init__(self, runtime: "BackendRuntime") -> None:
        self.runtime = runtime

    @property
    def ready(self) -> bool:
        return self.runtime.supports_speculation

    async def start(self, conv_id, request, prefill) -> None:
        await self.runtime.prefill(conv_id, request.history, template_kwargs=request.template_kwargs)

    async def propose(self, conv_id, token, k, recent=(), penalty=1.0):
        # Greedy whatever the temperature: the big model decides what is kept
        return await self.runtime.propose(conv_id, token, k, recent, penalty)

    async def follow(self, conv_id, emitted, proposal, proposed) -> None:
        if not proposed:  # it reads what was emitted, in one pass
            await self.runtime.decode_many(conv_id, emitted)
        elif len(emitted) <= len(proposal):  # it was fed the round's token and all but its last guess
            self.runtime.trim(conv_id, len(proposal) - len(emitted))
        else:  # all its guesses kept: it never fed its last one, so it catches up
            await self.runtime.decode_one(conv_id, proposal[-1])

    def finish(self, conv_id) -> None:
        self.runtime.reset_conversation(conv_id)


class SpeculativeGeneration(GenerationStrategy):
    name = "speculative"

    def __init__(self, draft: "Draft | BackendRuntime | None", draft_tokens: int = 4, adaptive: bool = False,
                 lookup: bool = False) -> None:
        if draft_tokens < 1:
            raise ValueError("draft_tokens must be at least 1")
        if draft is None and not lookup:
            raise ValueError("without a draft model, guesses can only be looked up")
        self.draft = draft if draft is None or isinstance(draft, Draft) else ModelDraft(draft)
        self.k = draft_tokens
        # Adaptive: guess up to `draft_tokens`, fewer after a check that threw guesses away (a check
        # costs more the more it checks), more again after one that kept them all.
        self.adaptive = adaptive
        # Lookup: when the last few tokens appeared earlier in the conversation, guess that what followed them
        # then follows them now. Free, and right whenever an answer quotes, copies or edits what it was given.
        self.lookup = lookup

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        target, draft = request.runtime, self.draft
        if not (target.supports_speculation and (draft is None or draft.ready)):
            return await AutoregressiveGeneration().generate(request)

        conv = request.conv_id
        prefill = await target.prefill(conv, request.history, forward_hidden=request.forward_hidden,
                                       template_kwargs=request.template_kwargs,
                                       keep_rows=draft.context_rows if draft is not None else 1)
        if prefill.logits_row is None:
            return GenerationResult(text="", stats={"prefill_tokens": 0, "decode_tokens": 0,
                                                    "prefill_ms": 0.0, "elapsed_ms": 0.0})
        if draft is not None:
            await draft.start(conv, request, prefill)

        out: list[int] = []

        def window(before: list[int]) -> list[int]:
            ctx = request.repetition_context
            return before[-ctx:] if ctx > 0 else before

        def sample(runtime: "BackendRuntime", logits_row, before: list[int]) -> int:
            return runtime.sample_token(logits_row, request.temperature, recent_tokens=window(before),
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

        k = self.k
        lookup = NGramLookup(prefill.context_ids) if self.lookup and prefill.context_ids else None
        looked_up = 0
        while len(out) < request.max_tokens and token not in stop_ids:
            rounds += 1
            if lookup is not None:
                lookup.append(token)  # `token` is emitted this round whatever the check says
            proposal = lookup.propose(self.k) if lookup is not None else []
            from_lookup = len(proposal) >= 2
            if from_lookup:
                looked_up += 1
            elif draft is None:  # nothing to look up and no draft: this round is one token, as without guessing
                proposal = []
            else:
                proposal = await draft.propose(conv, token, k, window(out + [token]), request.repetition_penalty)
            n_guess = len(proposal)
            proposed += n_guess
            if request.on_draft is not None and proposal:
                # `token` isn't shown yet either: it is emitted once the pool has checked the proposal
                await request.on_draft(_pending_text(target, out, _until_stop([token, *proposal], stop_ids)))

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
                next_token = sample(target, rows[n_guess], out + [token] + matched)

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
            if lookup is not None:
                for t in out[len(out) - emitted + 1:]:
                    lookup.append(t)
            if request.on_verified is not None:
                await request.on_verified(emitted - 1, n_guess)

            # Keep exactly what was emitted. The big model was fed 1 + n_guess tokens.
            await _trim(target, request, conv, (1 + n_guess) - emitted)
            if draft is not None:
                await draft.follow(conv, out[-emitted:], proposal, proposed=not from_lookup)
            if self.adaptive and not from_lookup:
                k = min(self.k, k + 1) if emitted > k else max(1, emitted)

            if stopped:
                break
            token = next_token

        tail = detok.finalize()
        if request.on_token is not None and tail:
            await request.on_token(tail)
        if request.on_draft is not None:
            await request.on_draft("")
        if draft is not None:
            draft.finish(conv)
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
                "lookup_rounds": looked_up,
            },
        )


class NGramLookup:
    """Where each short run of tokens last appeared, kept as tokens arrive, to guess what follows it again.

    `propose` looks up the last three tokens (then two) and returns what followed their latest earlier
    appearance: one dictionary lookup a size, however long the conversation.
    """

    def __init__(self, tokens: list[int], sizes: tuple[int, ...] = (3, 2)) -> None:
        self.tokens: list[int] = []
        self.sizes = sizes
        self.seen: dict[tuple[int, ...], int] = {}
        for t in tokens:
            self.append(t)

    def append(self, token: int) -> None:
        # The runs ending at the current last token get a continuation now, so they can be looked back to
        for n in self.sizes:
            if len(self.tokens) >= n:
                self.seen[tuple(self.tokens[-n:])] = len(self.tokens) - n
        self.tokens.append(token)

    def propose(self, k: int) -> list[int]:
        for n in self.sizes:
            if len(self.tokens) >= n:
                start = self.seen.get(tuple(self.tokens[-n:]))
                if start is not None:
                    return self.tokens[start + n:start + n + k]
        return []


def _until_stop(tokens: list[int], stop_ids: set[int]) -> list[int]:
    """The tokens before the first stop token: a draft guesses past the end, and that isn't shown."""
    for i, t in enumerate(tokens):
        if t in stop_ids:
            return tokens[:i]
    return tokens


def _pending_text(runtime: "BackendRuntime", emitted: list[int], pending: list[int]) -> str:
    """What `pending` adds after the emitted tokens. Decoded after the last emitted token, so the first
    pending word keeps its leading space (a tokenizer drops it when decoding a word on its own)."""
    anchor = emitted[-1:]
    before = runtime.decode_tokens(anchor)
    after = runtime.decode_tokens(anchor + pending)
    return after[len(before):] if after.startswith(before) else runtime.decode_tokens(pending)


async def _trim(target: "BackendRuntime", request: GenerationRequest, conv: str, n: int) -> None:
    if n <= 0:
        return
    target.trim(conv, n)
    if request.trim_remote is not None:
        await request.trim_remote(n)

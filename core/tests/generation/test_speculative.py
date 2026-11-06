"""Speculative decoding gives exactly the tokens one-at-a-time decoding gives, in fewer passes of the big model."""

from __future__ import annotations

import pytest

from dots.generation.autoregressive import AutoregressiveGeneration
from dots.generation.base import GenerationRequest
from dots.generation.speculative import SpeculativeGeneration

VOCAB = 40
STOP = 0


class Detok:
    def __init__(self):
        self.parts: list[str] = []

    def add_token(self, t: int) -> str:
        return f"<{t}>"

    def finalize(self) -> str:
        return ""


class FakeModel:
    """A deterministic 'language model' over integer tokens: its cache is the list of tokens fed.

    `rule(fed)` gives the next token after everything fed so far. Logits are one-hot on that token, so
    temperature-0 sampling picks it, which makes the expected output easy to know.
    """

    supports_speculation = True

    def __init__(self, rule, prompt=(5, 7)):
        self.rule = rule
        self.prompt = list(prompt)
        self.caches: dict[str, list[int]] = {}
        self.passes = 0

    def _row(self, fed: list[int]) -> list[float]:
        row = [0.0] * VOCAB
        row[self.rule(fed)] = 1.0
        return row

    async def prefill(self, conv, history, *, forward_hidden=None):
        from dots.backends.base import PrefillOutput

        self.passes += 1
        self.caches[conv] = list(self.prompt)
        return PrefillOutput(self._row(self.caches[conv]), len(self.prompt), 0.0)

    async def decode_one(self, conv, token, *, forward_hidden=None):
        self.passes += 1
        self.caches[conv].append(token)
        return self._row(self.caches[conv])

    async def decode_many(self, conv, tokens, *, forward_hidden=None):
        self.passes += 1
        rows = []
        for t in tokens:
            self.caches[conv].append(t)
            rows.append(self._row(self.caches[conv]))
        return rows

    def trim(self, conv, n):
        if n > 0:
            del self.caches[conv][-n:]

    def reset_conversation(self, conv):
        self.caches.pop(conv, None)

    def sample_token(self, row, temperature, recent_tokens=None, repetition_penalty=1.0):
        return max(range(VOCAB), key=lambda i: row[i])

    def stop_ids(self):
        return {STOP}

    def new_detokenizer(self):
        return Detok()

    def decode_tokens(self, tokens):
        return " ".join(map(str, tokens))


def target_rule(fed: list[int]) -> int:
    """Counts up from the last token, skipping the stop token, and stops after the 30th token."""
    if len(fed) >= 32:
        return STOP
    return fed[-1] % (VOCAB - 1) + 1


def draft_rule_every_third_wrong(fed: list[int]) -> int:
    guess = target_rule(fed)
    return guess if len(fed) % 3 else (guess % (VOCAB - 1)) + 1


def request(runtime, **kw) -> GenerationRequest:
    return GenerationRequest(runtime=runtime, conv_id="c", history=[], temperature=0.0,
                             repetition_penalty=1.0, **kw)


async def autoregressive(max_tokens=100):
    target = FakeModel(target_rule)
    result = await AutoregressiveGeneration().generate(request(target, max_tokens=max_tokens))
    return result, target


@pytest.mark.parametrize("k", [1, 2, 4, 7])
@pytest.mark.parametrize("draft_rule", [target_rule, draft_rule_every_third_wrong, lambda fed: 3])
async def test_same_tokens_as_one_at_a_time(k, draft_rule):
    expected, ar_target = await autoregressive()
    target, draft = FakeModel(target_rule), FakeModel(draft_rule)
    result = await SpeculativeGeneration(draft, draft_tokens=k).generate(request(target, max_tokens=100))
    assert result.text == expected.text
    assert target.caches["c"] == ar_target.caches["c"], "the cache holds exactly the emitted tokens"


async def test_a_good_draft_needs_far_fewer_passes():
    expected, ar_target = await autoregressive()
    target, draft = FakeModel(target_rule), FakeModel(target_rule)
    result = await SpeculativeGeneration(draft, draft_tokens=4).generate(request(target, max_tokens=100))
    assert result.text == expected.text
    assert result.stats["verify_passes"] * 4 <= result.stats["decode_tokens"] + 4
    assert target.passes < ar_target.passes / 3


@pytest.mark.parametrize("limit", [1, 3, 5, 9])
async def test_the_length_limit_is_exact(limit):
    expected, _ = await autoregressive(max_tokens=limit)
    target, draft = FakeModel(target_rule), FakeModel(target_rule)
    result = await SpeculativeGeneration(draft, draft_tokens=4).generate(request(target, max_tokens=limit))
    assert result.text == expected.text and result.stats["decode_tokens"] == limit


async def test_remote_caches_are_trimmed_by_the_same_amount():
    trims: list[int] = []

    async def trim_remote(n: int) -> None:
        trims.append(n)

    target, draft = FakeModel(target_rule), FakeModel(draft_rule_every_third_wrong)
    await SpeculativeGeneration(draft, draft_tokens=4).generate(request(target, max_tokens=100, trim_remote=trim_remote))
    assert trims and all(n > 0 for n in trims)


async def test_falls_back_when_a_backend_cannot_verify_several_tokens():
    expected, _ = await autoregressive()
    target, draft = FakeModel(target_rule), FakeModel(target_rule)
    draft.supports_speculation = False
    result = await SpeculativeGeneration(draft).generate(request(target, max_tokens=100))
    assert result.text == expected.text and "verify_passes" not in result.stats

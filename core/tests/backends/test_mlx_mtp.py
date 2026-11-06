"""A model's own draft head guesses from the hidden states the pool ran, and stays in step through checks.

Tiny Qwen3.5 models (dense and MoE) with random weights, in float32 so near-ties don't flip a guess. After
any mix of checks, kept and rejected guesses and rounds it sat out, the head must guess what a head fed
every (hidden state, next token) pair of what was kept, from scratch, guesses.
"""

import json
import sys
from pathlib import Path

import pytest

mx = pytest.importorskip("mlx.core")
utils = pytest.importorskip("mlx_lm.utils")

from dots.backends.base import PrefillOutput  # noqa: E402
from dots.backends.mlx.mtp import MtpDraft, MtpHead  # noqa: E402
from dots.backends.mlx.runtime import FullModelHost, MlxBackendRuntime  # noqa: E402

ARCHITECTURES = Path(__file__).parent / "architectures"
PROMPT = [1, 5, 9, 13, 2, 7, 11, 3, 4, 6, 8, 10]


@pytest.fixture
def real_mlx(monkeypatch):
    """The suite fakes MLX's cache and arrays for its worker tests; these models need the real ones."""
    from mlx_lm.models.cache import make_prompt_cache

    monkeypatch.setattr("dots.backends.mlx.runtime.make_prompt_cache", make_prompt_cache)
    monkeypatch.setattr("dots.backends.mlx.runtime.array", mx.array)


def _model_and_head(name: str):
    cfg = json.loads((ARCHITECTURES / f"{name}.json").read_text())
    model_class, args_class = utils._get_classes(config=cfg)
    mx.random.seed(0)
    model = model_class(args_class.from_dict(cfg))
    host = FullModelHost(model, None)
    layer_cls = sys.modules[type(host.text).__module__].DecoderLayer
    head = MtpHead(layer_cls, model.language_model.args)
    mx.eval(model.parameters(), head.parameters())
    return host, head


def _reference(host, head, sequence: list[int], token: int):
    """A head fed every pair of `sequence`, then `token`, in one go: its cache and its first guess."""
    from mlx_lm.models.cache import KVCache

    ids = mx.array([sequence])
    rows = host.norm(host.forward_segment("ref", host.embed(ids), 0, host.n_layers))
    host.reset_conversation("ref")
    cache = [KVCache() for _ in head.layers]
    out = head(host.embed(mx.array([[*sequence[1:], token]])), rows, cache)
    return cache[0], int(mx.argmax(host._project_logits(out[:, -1:, :])[0, -1]).item())


def _greedy(row) -> int:
    return int(mx.argmax(row).item())


@pytest.mark.parametrize("name", ["qwen3_5", "qwen3_5_moe"])
async def test_head_stays_in_step_through_checks(name, real_mlx):
    host, head = _model_and_head(name)
    target = MlxBackendRuntime.from_host(host)
    draft = MtpDraft(target, head)
    logits = await target.decode_many("c", PROMPT)  # the prompt, keeping every row as a prefill would
    await draft.start("c", None, PrefillOutput(None, len(PROMPT), 0.0, context_ids=list(PROMPT)))
    emitted = list(PROMPT)
    token = _greedy(logits[-1])
    # A check keeps whatever it keeps: none of the guesses, some, all. Every fourth round the guesses come
    # from somewhere else and the head sits it out.
    for round_ in range(12):
        sat_out = round_ % 4 == 3
        if sat_out:
            proposal = [7, 8, 9]
        else:
            proposal = await draft.propose("c", token, 3)
            cache, guess = _reference(host, head, emitted, token)
            assert proposal[0] == guess
            # Its cache holds those pairs, then the entries its last two guesses added
            ours = draft._turns["c"].cache[0]
            assert ours.offset == cache.offset + 2
            assert mx.allclose(ours.keys[..., :cache.offset, :], cache.keys[..., :cache.offset, :], atol=1e-4)
        rows = await target.decode_many("c", [token, *proposal])
        kept = [token, *proposal[:round_ % 4]]
        target.trim("c", 1 + len(proposal) - len(kept))
        await draft.follow("c", kept, proposal, proposed=not sat_out)
        emitted += kept
        token = _greedy(rows[len(kept) - 1])
    draft.finish("c")

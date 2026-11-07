"""Every architecture dots serves runs split across Macs exactly as it runs whole.

Each config is a current model's (Qwen3.5 and 3.6, LFM2.5, gpt-oss, MiniMax M2.7, GLM 5.3, Kimi K2.5),
shrunk to a few small layers and
filled with random weights: the layer kinds, their caches and masks are the real ones. The model runs in
two halves through dots' host, as two Macs would, and must give the logits it gives in one piece.
"""

import json
from pathlib import Path

import pytest

mx = pytest.importorskip("mlx.core")
utils = pytest.importorskip("mlx_lm.utils")

from dots.backends.mlx.runtime import FullModelHost  # noqa: E402

CONFIGS = sorted((Path(__file__).parent / "architectures").glob("*.json"))
SLIDING_WINDOW = {"gpt_oss"}
REWINDABLE = [p for p in CONFIGS if p.stem not in SLIDING_WINDOW]


@pytest.fixture
def real_mlx(monkeypatch):
    """The suite fakes MLX's cache and arrays for its worker tests; these models need the real ones."""
    from mlx_lm.models.cache import make_prompt_cache

    monkeypatch.setattr("dots.backends.mlx.runtime.make_prompt_cache", make_prompt_cache)
    monkeypatch.setattr("dots.backends.mlx.runtime.array", mx.array)


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_split_in_two_matches_whole(path, real_mlx):
    cfg = json.loads(path.read_text())
    model_class, args_class = utils._get_classes(config=cfg)
    mx.random.seed(0)
    model = model_class(args_class.from_dict(cfg))
    mx.eval(model.parameters())
    ids = mx.array([[1, 5, 9, 13, 2, 7, 11, 3, 4, 6, 8, 10]])  # longer than the sliding windows
    whole = model(ids).astype(mx.float32)[:, -1]

    host = FullModelHost(model, None)
    half = host.n_layers // 2
    hidden = host.forward_segment("c", host.embed(ids), 0, half)
    hidden = host.forward_segment("c", hidden, half, host.n_layers)
    split = host._project_logits(host.norm(hidden)[:, -1:, :]).astype(mx.float32)[:, -1]

    assert mx.max(mx.abs(whole - split)).item() < 1e-3
    # A sliding window can't be rolled back once it wraps, so those models don't guess ahead
    assert host.can_rewind == (path.stem not in SLIDING_WINDOW)


@pytest.mark.parametrize("path", REWINDABLE, ids=lambda p: p.stem)
def test_rolling_back_rejected_guesses_is_as_if_they_never_came(path, real_mlx):
    """A check feeds three tokens; two are rejected and rolled back; the next token then gives what a model
    that only ever saw the accepted one gives. For linear attention and convolution this is the running
    state put back, not a cache truncated."""
    cfg = json.loads(path.read_text())
    model_class, args_class = utils._get_classes(config=cfg)
    mx.random.seed(0)
    model = model_class(args_class.from_dict(cfg))
    mx.eval(model.parameters())
    prompt = mx.array([[1, 5, 9, 13, 2, 7, 11, 3, 4, 6, 8, 10]])

    def run(host, conv, ids):
        out = host.forward_segment(conv, host.embed(ids), 0, host.n_layers)
        return host._project_logits(host.norm(out)[:, -1:, :]).astype(mx.float32)[0, -1]

    checked = FullModelHost(model, None)
    run(checked, "c", prompt)
    run(checked, "c", mx.array([[20, 21, 22]]))  # a check of three tokens
    checked.trim("c", 2)  # only the first was right
    after_rollback = run(checked, "c", mx.array([[30]]))

    clean = FullModelHost(model, None)
    run(clean, "c", prompt)
    run(clean, "c", mx.array([[20]]))
    expected = run(clean, "c", mx.array([[30]]))

    assert mx.max(mx.abs(after_rollback - expected)).item() < 1e-3

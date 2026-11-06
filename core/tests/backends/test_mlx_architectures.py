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
# Kinds whose state can't be rolled back, so they decode one token at a time instead of guessing ahead
RUNNING_STATE = {"qwen3_5", "qwen3_5_moe", "lfm2", "lfm2_moe"}


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
    assert host.can_rewind == (path.stem not in RUNNING_STATE)

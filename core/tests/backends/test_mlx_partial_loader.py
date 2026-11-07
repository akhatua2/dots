"""Regression test for `dots.partial_loader.load_partial`.

The partial loader is the riskiest piece of the dots architecture
because subtle bugs (missing quantization sidecar, wrong tying detection,
silently-dropped modules) manifest as plausible-looking garbage output
rather than crashes. We catch those by comparing partial-load to a
known-good baseline:

  • `mlx_lm.load(model_id)` is the ground truth.
  • `load_partial(model_id, layer_start=0, layer_end=n_layers, needs_head=True)`
    is the partial loader covering the full model.

Both must produce identical token streams on a fixed prompt with greedy
sampling. If they ever diverge, this test fails — protecting every
narrower partial-load case downstream.

This test downloads a real (small) model on first run. Skipped if
HF download fails (offline CI, no network).
"""

from __future__ import annotations

import pytest

from tests.helpers.mlx_env import mlx_available_reason


SMOKE_MODEL = "mlx-community/Qwen3.5-0.8B-4bit"
MLX_SKIP_REASON = mlx_available_reason()
if MLX_SKIP_REASON is not None:
    pytestmark = pytest.mark.skip(reason=MLX_SKIP_REASON)


@pytest.fixture(autouse=True)
def real_mlx(monkeypatch):
    """The suite fakes MLX's cache and arrays for its worker tests; these load a real model."""
    import mlx.core as mx
    from mlx_lm.models.cache import make_prompt_cache

    monkeypatch.setattr("dots.backends.mlx.runtime.make_prompt_cache", make_prompt_cache)
    monkeypatch.setattr("dots.backends.mlx.runtime.array", mx.array)


def _host(model, tokenizer):
    """dots' own host over a model, which knows each architecture's layout, caches and masks."""
    from dots.backends.mlx.runtime import FullModelHost

    return FullModelHost(model, tokenizer)


def _prompt_ids(tokenizer, text: str):
    import mlx.core as mx

    return mx.array([tokenizer.apply_chat_template([{"role": "user", "content": text}], add_generation_prompt=True)])


def _greedy_argmax_first_token(model, tokenizer, prompt_text: str) -> int:
    """One prefill of `prompt_text` and the argmax of the last position. Greedy, so directly comparable."""
    import mlx.core as mx

    host = _host(model, tokenizer)
    hidden = host._forward(_prompt_ids(tokenizer, prompt_text), host._get_cache("c"))
    return int(mx.argmax(host._project_logits(hidden[:, -1:, :])[0, -1]).item())


@pytest.mark.real_model
def test_partial_full_matches_mlx_lm_load():
    """load_partial(full range, head=True) must produce the same first
    sampled token as mlx_lm.load on a fixed prompt."""
    try:
        from mlx_lm import load as mlx_load
    except ImportError:
        pytest.skip("mlx_lm not installed")

    from dots.backends.mlx.layout import text_model
    from dots.backends.mlx.partial_loader import load_partial

    try:
        full_model, full_tok = mlx_load(SMOKE_MODEL)
    except Exception as e:
        pytest.skip(f"could not download/load {SMOKE_MODEL}: {e}")

    partial_model, partial_tok, n_layers = load_partial(SMOKE_MODEL, layer_start=0, layer_end=None, needs_head=True)
    assert n_layers == len(text_model(full_model).layers)

    full_tok_id = _greedy_argmax_first_token(full_model, full_tok, "Hello")
    partial_tok_id = _greedy_argmax_first_token(partial_model, partial_tok, "Hello")
    assert full_tok_id == partial_tok_id, (
        f"partial loader diverged from mlx_lm.load on first token: full={full_tok_id}, partial={partial_tok_id}"
    )


@pytest.mark.real_model
def test_partial_chain_matches_full():
    """Split the model: the head loads layers 0..k and the head modules, the chain loads k..N. The head's
    layers then the chain's, on the same hidden state, must give the full model's next token."""
    try:
        from mlx_lm import load as mlx_load
    except ImportError:
        pytest.skip("mlx_lm not installed")

    import mlx.core as mx

    from dots.backends.mlx.layout import text_model
    from dots.backends.mlx.partial_loader import load_partial

    try:
        full_model, tokenizer = mlx_load(SMOKE_MODEL)
    except Exception as e:
        pytest.skip(f"could not download/load {SMOKE_MODEL}: {e}")

    n = len(text_model(full_model).layers)
    split = n // 2
    head = _host(load_partial(SMOKE_MODEL, layer_start=0, layer_end=split, needs_head=True)[0], tokenizer)
    chain = _host(load_partial(SMOKE_MODEL, layer_start=split, layer_end=n, needs_head=False)[0], tokenizer)
    full = _host(full_model, tokenizer)

    ids = _prompt_ids(tokenizer, "Hi")
    expected = full._project_logits(full._forward(ids, full._get_cache("f"))[:, -1:, :])
    hidden = head.forward_segment("c", head.embed(ids), 0, split)
    hidden = chain.forward_segment("c", hidden, split, n)
    got = head._project_logits(head.norm(hidden)[:, -1:, :])
    assert int(mx.argmax(got[0, -1]).item()) == int(mx.argmax(expected[0, -1]).item())

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


SMOKE_MODEL = "mlx-community/Qwen2.5-0.5B-Instruct-4bit"
MLX_SKIP_REASON = mlx_available_reason()
if MLX_SKIP_REASON is not None:
    pytestmark = pytest.mark.skip(reason=MLX_SKIP_REASON)


def _greedy_argmax_first_token(model, tokenizer, prompt_text: str) -> int:
    """Run one prefill on `prompt_text` and return the argmax token of the
    final-position logits. Greedy = deterministic = directly comparable."""
    import mlx.core as mx
    from mlx_lm.models.base import create_attention_mask
    from mlx_lm.models.cache import make_prompt_cache

    ids = tokenizer.apply_chat_template(
        [{"role": "user", "content": prompt_text}],
        add_generation_prompt=True,
    )
    ids_arr = mx.array([ids])
    cache = make_prompt_cache(model)
    hidden = model.model.embed_tokens(ids_arr)
    mask = create_attention_mask(hidden, cache)
    for i, layer in enumerate(model.model.layers):
        hidden = layer(hidden, mask=mask, cache=cache[i])
    hidden = model.model.norm(hidden)
    if model.args.tie_word_embeddings:
        logits = model.model.embed_tokens.as_linear(hidden[:, -1:, :])
    else:
        logits = model.lm_head(hidden[:, -1:, :])
    return int(mx.argmax(logits[0, -1], axis=-1).item())


@pytest.mark.real_model
def test_partial_full_matches_mlx_lm_load():
    """load_partial(full range, head=True) must produce the same first
    sampled token as mlx_lm.load on a fixed prompt."""
    try:
        from mlx_lm import load as mlx_load
    except ImportError:
        pytest.skip("mlx_lm not installed")

    from dots.backends.mlx.partial_loader import load_partial

    try:
        full_model, full_tok = mlx_load(SMOKE_MODEL)
    except Exception as e:
        pytest.skip(f"could not download/load {SMOKE_MODEL}: {e}")

    partial_model, partial_tok, n_layers = load_partial(
        SMOKE_MODEL,
        layer_start=0,
        layer_end=None,
        needs_head=True,
    )
    assert n_layers == len(full_model.model.layers)

    prompt = "Hello"
    full_tok_id = _greedy_argmax_first_token(full_model, full_tok, prompt)
    partial_tok_id = _greedy_argmax_first_token(partial_model, partial_tok, prompt)
    assert full_tok_id == partial_tok_id, (
        f"partial loader diverged from mlx_lm.load on first token: "
        f"full={full_tok_id}, partial={partial_tok_id}"
    )


@pytest.mark.real_model
def test_partial_chain_matches_full():
    """Split the model: head loads layers 0..k + head modules; chain
    loads layers k..N. Running head's forward + chain's forward_segment
    on the same hidden should match the full model's forward."""
    try:
        from mlx_lm import load as mlx_load
    except ImportError:
        pytest.skip("mlx_lm not installed")

    import mlx.core as mx
    from mlx_lm.models.base import create_attention_mask
    from mlx_lm.models.cache import make_prompt_cache

    from dots.backends.mlx.partial_loader import load_partial

    try:
        full_model, tokenizer = mlx_load(SMOKE_MODEL)
    except Exception as e:
        pytest.skip(f"could not download/load {SMOKE_MODEL}: {e}")

    n = len(full_model.model.layers)
    split = n // 2

    head_model, _, _ = load_partial(
        SMOKE_MODEL,
        layer_start=0,
        layer_end=split,
        needs_head=True,
    )
    chain_model, _, _ = load_partial(
        SMOKE_MODEL,
        layer_start=split,
        layer_end=n,
        needs_head=False,
    )

    # Build identical prompts and run each model independently with
    # appropriate caches.
    ids = tokenizer.apply_chat_template(
        [{"role": "user", "content": "Hi"}],
        add_generation_prompt=True,
    )
    ids_arr = mx.array([ids])

    # Full model reference.
    full_cache = make_prompt_cache(full_model)
    full_hidden = full_model.model.embed_tokens(ids_arr)
    full_mask = create_attention_mask(full_hidden, full_cache)
    for i, layer in enumerate(full_model.model.layers):
        full_hidden = layer(full_hidden, mask=full_mask, cache=full_cache[i])
    full_hidden = full_model.model.norm(full_hidden)
    if full_model.args.tie_word_embeddings:
        full_logits = full_model.model.embed_tokens.as_linear(full_hidden[:, -1:, :])
    else:
        full_logits = full_model.lm_head(full_hidden[:, -1:, :])
    full_token = int(mx.argmax(full_logits[0, -1], axis=-1).item())

    # Split forward.
    head_cache = make_prompt_cache(head_model)
    chain_cache = make_prompt_cache(chain_model)
    hidden = head_model.model.embed_tokens(ids_arr)
    head_mask = create_attention_mask(hidden, head_cache[:split])
    for offset in range(split):
        hidden = head_model.model.layers[offset](
            hidden,
            mask=head_mask,
            cache=head_cache[offset],
        )
    # Chain segment runs layers [split, n).
    chain_mask = create_attention_mask(hidden, chain_cache[split:n])
    for offset, layer in enumerate(chain_model.model.layers[split:n]):
        hidden = layer(hidden, mask=chain_mask, cache=chain_cache[split + offset])
    # Back to head for norm + lm_head + sample.
    hidden = head_model.model.norm(hidden)
    if head_model.args.tie_word_embeddings:
        split_logits = head_model.model.embed_tokens.as_linear(hidden[:, -1:, :])
    else:
        split_logits = head_model.lm_head(hidden[:, -1:, :])
    split_token = int(mx.argmax(split_logits[0, -1], axis=-1).item())

    assert full_token == split_token, (
        f"split partial-load diverged from full: full={full_token}, split={split_token}"
    )

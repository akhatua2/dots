"""Draft models for speculative decoding: a small model loaded whole on the head Mac.

It has to share the big model's tokenizer, so that its proposals are the big model's token ids.
"""

from __future__ import annotations

from ..base import BackendRuntime


def load_draft_runtime(repo: str) -> BackendRuntime:
    """Loads `repo` whole (it is small) and wraps it as a runtime the generation strategies can drive."""
    from mlx_lm import load

    from .runtime import FullModelHost, MlxBackendRuntime

    model, tokenizer = load(repo)
    return MlxBackendRuntime.from_host(FullModelHost(model, tokenizer))


def same_tokenizer(draft: BackendRuntime, target: BackendRuntime) -> bool:
    """True when both models turn text into the same token ids."""
    a = getattr(getattr(draft, "host", None), "tokenizer", None)
    b = getattr(getattr(target, "host", None), "tokenizer", None)
    if a is None or b is None:
        return False
    return a.vocab_size == b.vocab_size and a.eos_token_id == b.eos_token_id

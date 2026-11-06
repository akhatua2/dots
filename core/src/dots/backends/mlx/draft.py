"""What guesses ahead on the head Mac: the big model's own draft head, or a small model loaded whole.

A small model has to share the big model's tokenizer, so that its proposals are the big model's token ids.
"""

from __future__ import annotations

from ..base import BackendRuntime


def load_draft_runtime(repo: str) -> BackendRuntime:
    """Loads `repo` whole (it is small) and wraps it as a runtime the generation strategies can drive."""
    from mlx_lm import load

    from .runtime import FullModelHost, MlxBackendRuntime

    model, tokenizer = load(repo)
    return MlxBackendRuntime.from_host(FullModelHost(model, tokenizer))


def load_draft(repo: str, target: BackendRuntime):
    """`repo` as a draft for `target`: its draft head when `repo` holds one, otherwise a small model."""
    from .mtp import holds_head, load_head

    if holds_head(repo):
        return load_head(repo, target.host)
    return load_draft_runtime(repo)


def draft_for(loaded, target: BackendRuntime, *, spans_macs: bool):
    """The draft for one turn, or None. The model's own head costs one layer a guess and pays on one Mac
    too (Qwen3.5 4B: 1.48x with no network, 1.41x at 25 ms). A separate model costs a pass of its own and
    a bigger check, which only pays once a turn crosses the network (0.85x on one Mac, 1.26x at 25 ms)."""
    from ...generation.speculative import ModelDraft
    from .mtp import MtpDraft, MtpHead

    if isinstance(loaded, MtpHead):
        return MtpDraft(target, loaded)
    if loaded is not None and spans_macs and same_tokenizer(loaded, target):
        return ModelDraft(loaded)
    return None


def same_tokenizer(draft: BackendRuntime, target: BackendRuntime) -> bool:
    """True when both models turn text into the same token ids."""
    a = getattr(getattr(draft, "host", None), "tokenizer", None)
    b = getattr(getattr(target, "host", None), "tokenizer", None)
    if a is None or b is None:
        return False
    return a.vocab_size == b.vocab_size and a.eos_token_id == b.eos_token_id

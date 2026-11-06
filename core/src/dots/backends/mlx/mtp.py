"""A model's own draft head (multi-token prediction): guesses ahead without a second model.

Qwen3.5 and its successors were trained with one extra decoder layer that reads the model's last hidden
state at a position together with the token that came next, and predicts the token after that. The head
Mac already has everything it needs besides that layer: the embedding, the final norm, the output layer
and the hidden state of every position the pool just ran. So a guess costs one layer and the output
layer here, and no network.

The layer keeps its own attention cache, one entry per (hidden state, next token) pair. Its guesses past
the first feed its own output back in place of the model's hidden state, which is close but not the same,
so after each check those entries are replaced by ones built from the hidden states the pool computed.

The weights come in two layouts: Qwen's own release keeps them inside the model under `mtp.` with
zero-centred norms (mlx-lm drops them on load), and mlx-community publishes them alone as a small
`*-MTP-*` repo, already converted. Either can be named as `DOTS_DRAFT_MODEL`.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

import mlx.core as mx
import mlx.nn as nn

from ...generation.speculative import Draft

# How many of the prompt's last positions the head reads at the start of a turn. On Qwen3.5 0.8B the last
# 32 were as good as the whole prompt (78% of first guesses kept) and none at all lost five points.
CONTEXT_ROWS = 32


class MtpHead(nn.Module):
    def __init__(self, layer_cls, args, n_layers: int = 1):
        super().__init__()
        size, eps = args.hidden_size, args.rms_norm_eps
        self.fc = nn.Linear(2 * size, size, bias=False)
        self.pre_fc_norm_embedding = nn.RMSNorm(size, eps=eps)
        self.pre_fc_norm_hidden = nn.RMSNorm(size, eps=eps)
        # Always a full-attention layer: the index picks that kind in models that mix kinds
        self.layers = [layer_cls(args, layer_idx=getattr(args, "full_attention_interval", 1) - 1)
                       for _ in range(n_layers)]
        self.norm = nn.RMSNorm(size, eps=eps)

    def __call__(self, embedded, hidden, cache):
        h = self.fc(mx.concatenate([self.pre_fc_norm_embedding(embedded), self.pre_fc_norm_hidden(hidden)], axis=-1))
        mask = "causal" if h.shape[1] > 1 else None
        for layer, layer_cache in zip(self.layers, cache):
            h = layer(h, mask, layer_cache)
        return self.norm(h)


HEAD_TENSORS = ("pre_fc_norm_hidden.weight", "mtp.pre_fc_norm_hidden.weight")


def _weight_files(repo: str) -> dict[str, str]:
    """Tensor name -> file, read from the safetensors headers alone (a few KB, nothing downloaded)."""
    from huggingface_hub import get_safetensors_metadata

    return get_safetensors_metadata(repo).weight_map


def holds_head(repo: str) -> bool:
    """True when `repo` holds a model's draft head, alone or inside a full release, rather than being a
    separate draft model. A converted model whose config still mentions its head but whose weights dropped
    it doesn't count. Whether it is the right model's head is checked when it loads."""
    names = _weight_files(repo)
    return any(name in names for name in HEAD_TENSORS)


def _args(host):
    from .layout import output_owner

    return output_owner(host.model).args


def _weights(repo: str) -> tuple[dict, dict]:
    """The head's tensors, named as `MtpHead` names them, and the repo's config."""
    from huggingface_hub import snapshot_download

    names = _weight_files(repo)
    inside_model = HEAD_TENSORS[1] in names
    # Inside a full release only the files holding the head are fetched
    files = sorted({f for k, f in names.items() if k.startswith("mtp.") or not inside_model})
    path = snapshot_download(repo, allow_patterns=["config.json", *files])
    with open(os.path.join(path, "config.json")) as f:
        config = json.load(f)
    weights = {}
    for file in files:
        weights.update(mx.load(os.path.join(path, file)))
    if inside_model:
        # Qwen's release: its norms are stored centred on zero, as mlx-lm's own conversion corrects for
        weights = {k[len("mtp."):]: v for k, v in weights.items() if k.startswith("mtp.")}
        weights = {k: v + 1.0 if "norm" in k and v.ndim == 1 else v for k, v in weights.items()}
    return weights, config


def load_head(repo: str, host) -> MtpHead:
    """The draft head in `repo`, built with the same layer code as `host`'s model."""
    weights, config = _weights(repo)
    args = _args(host)
    layer_cls = getattr(sys.modules[type(host.text).__module__], "DecoderLayer", None)
    if layer_cls is None or "pre_fc_norm_hidden.weight" not in weights:
        raise ValueError(f"{repo}: dots doesn't know this kind of draft head")
    if weights["pre_fc_norm_hidden.weight"].shape[0] != args.hidden_size:
        raise ValueError(f"{repo} is the draft head of a different model")
    n_layers = len({k.split(".")[1] for k in weights if k.startswith("layers.")})
    head = MtpHead(layer_cls, args, n_layers)
    quant = config.get("quantization")
    if quant:
        nn.quantize(head, quant["group_size"], quant["bits"], mode=quant.get("mode", "affine"),
                    class_predicate=lambda path, _module: f"{path}.scales" in weights)
    head.load_weights(list(weights.items()), strict=True)
    mx.eval(head.parameters())
    return head


@dataclass
class _Turn:
    cache: list
    # Hidden states the head hasn't read yet: rows[i] pairs with tokens[i], and the last row, one past the
    # tokens, is the position whose next token is still to come
    rows: mx.array
    tokens: list[int] = field(default_factory=list)
    guessed: int = 0  # cache entries the last guesses added from the head's own outputs


class MtpDraft(Draft):
    """Guesses with the big model's own draft head, on the head Mac, from the hidden states the pool ran."""

    context_rows = CONTEXT_ROWS
    # Each guess past the first reads the head's own output, and fewer hold. On Qwen3.5 4B up to one a check
    # was fastest with no network and up to three at 25 ms (scripts/bench_decode.py); two is close to both.
    most_guesses = 2

    def __init__(self, target, head: MtpHead):
        from mlx_lm.models.cache import KVCache

        self.target = target
        self.head = head
        self._new_cache = lambda: [KVCache() for _ in head.layers]
        self._turns: dict[str, _Turn] = {}

    @property
    def ready(self) -> bool:
        return self.target.supports_speculation

    async def start(self, conv_id, request, prefill) -> None:
        rows = self.target.last_rows(conv_id)
        context = prefill.context_ids or []
        n = min(rows.shape[1], len(context))
        tokens = context[len(context) - n + 1:] if n > 1 else []
        self._turns[conv_id] = _Turn(self._new_cache(), rows[:, rows.shape[1] - n:], tokens)

    async def propose(self, conv_id: str, token: int, k: int, recent: list[int] = (),
                      penalty: float = 1.0) -> list[int]:
        """k guesses after `token`: the first from the pool's own hidden state, the rest each from the last.
        Each avoids repeats as the big model does, or a penalized model would rarely keep them."""
        from .runtime import penalized

        turn = self._turns[conv_id]
        host = self.target.host
        tokens = mx.array([[*turn.tokens, token]])
        out = self.head(host.embed(tokens), turn.rows, turn.cache)[:, -1:, :]
        seen = mx.array(list(recent), dtype=mx.int32)
        guesses = []
        for i in range(k):
            logits = penalized(host._project_logits(out)[:, -1, :], seen, penalty)
            guess = mx.argmax(logits, axis=-1, keepdims=True)
            seen = mx.concatenate([seen, guess[0]])
            guesses.append(guess)
            if i < k - 1:
                out = self.head(host.embed(guess), out, turn.cache)
        picked = mx.concatenate(guesses, axis=1)
        mx.eval(picked)
        turn.rows, turn.tokens, turn.guessed = turn.rows[:, :0], [], k - 1
        return [int(t) for t in picked[0].tolist()]

    async def follow(self, conv_id: str, emitted: list[int], proposal: list[int], proposed: bool) -> None:
        """After a check: the guesses' own entries go, and the pool's hidden states for what was kept come in."""
        turn = self._turns[conv_id]
        if proposed:
            for c in turn.cache:
                c.trim(turn.guessed)
            turn.guessed = 0
        checked = self.target.last_rows(conv_id)[:, :len(emitted)]
        turn.rows = mx.concatenate([turn.rows, checked], axis=1)
        turn.tokens += emitted[1:] if proposed else emitted

    def finish(self, conv_id: str) -> None:
        self._turns.pop(conv_id, None)

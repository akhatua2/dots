"""Minimal stand-ins for the mlx-lm model + tokenizer + prompt cache.

The point of these fakes is to make our tests verify the *plumbing* of dots —
protocol framing, peer connection lifecycle, pool re-planning, attention to
layer ordering, KV-cache reuse across turns, recovery from worker drops —
without paying the cost of loading a real transformer. Tests using fakes
run in well under a second.

Each `FakeLayer` increments its cache's length counter when called, so a
test can assert that a particular layer ran exactly N times by checking
its cache's `len`. The `FakeEmbed.as_linear` returns logits where argmax
always picks token id 5 (any non-EOS, non-zero choice); pair with the
fake tokenizer's `EOS=99` to control loop termination via max_tokens or
explicit injection.
"""

from __future__ import annotations


VOCAB_SIZE = 128
HIDDEN_DIM = 16
DEFAULT_LAYERS = 8

FAKE_NEXT_TOKEN = 5  # what every greedy sample produces
EOS_TOKEN = 99  # tokenizer.eos_token_id


class FakeTensor:
    """Tiny tensor stand-in for mocked tests.

    It carries shape and an optional greedy token id. The fake model only
    needs shape propagation, indexing, and wire round-trips; numeric MLX
    behavior belongs in real-model tests.
    """

    def __init__(
        self,
        shape: tuple[int, ...],
        *,
        argmax_token: int | None = None,
    ):
        self.shape = shape
        self.argmax_token = argmax_token

    @classmethod
    def from_nested(cls, data) -> "FakeTensor":
        def _shape(value) -> tuple[int, ...]:
            if isinstance(value, list):
                if not value:
                    return (0,)
                return (len(value), *_shape(value[0]))
            return ()

        return cls(_shape(data))

    @classmethod
    def from_wire(cls, payload: dict) -> "FakeTensor":
        return cls(
            tuple(payload["shape"]),
            argmax_token=payload.get("argmax_token"),
        )

    def to_wire(self) -> dict:
        return {
            "encoding": "fake",
            "shape": list(self.shape),
            "argmax_token": self.argmax_token,
        }

    def __getitem__(self, key):
        if not isinstance(key, tuple):
            key = (key,)
        new_shape: list[int] = []
        for i, dim in enumerate(self.shape):
            part = key[i] if i < len(key) else slice(None)
            if isinstance(part, int):
                continue
            if isinstance(part, slice):
                start, stop, step = part.indices(dim)
                new_shape.append(len(range(start, stop, step)))
            else:
                new_shape.append(dim)
        return FakeTensor(tuple(new_shape), argmax_token=self.argmax_token)


class FakeKVCache:
    """Tracks how many positions have been pushed through us. dots reads
    nothing else from the cache (the real attention layer is bypassed)."""

    def __init__(self):
        self.len = 0
        self.offset = 0


class FakeLayer:
    """A transformer block that just records each call and passes hidden
    states through unchanged. The cache it gets extended is the assertion
    point for tests verifying that layers really executed."""

    def __init__(self):
        self.call_count = 0

    def __call__(self, hidden, mask=None, cache=None):
        self.call_count += 1
        if cache is not None:
            seq = hidden.shape[1] if len(hidden.shape) > 1 else 1
            cache.len += seq
            cache.offset = cache.len
        return hidden


class FakeEmbed:
    """Stands in for `embed_tokens`. As an embedding layer it returns zeros
    with the right shape; as `as_linear` it returns logits whose argmax is
    always `FAKE_NEXT_TOKEN`."""

    def __init__(self, hidden_dim: int = HIDDEN_DIM, vocab_size: int = VOCAB_SIZE):
        self.hidden_dim = hidden_dim
        self.vocab_size = vocab_size

    def __call__(self, tokens: FakeTensor) -> FakeTensor:
        # tokens: [batch, seq] -> hidden: [batch, seq, hidden_dim]
        if len(tokens.shape) == 1:
            seq = tokens.shape[0]
            batch = 1
        else:
            batch, seq = tokens.shape[0], tokens.shape[1]
        return FakeTensor((batch, seq, self.hidden_dim))

    def as_linear(self, hidden: FakeTensor) -> FakeTensor:
        # hidden: [batch, seq, hidden_dim] -> logits: [batch, seq, vocab]
        batch = hidden.shape[0]
        seq = hidden.shape[1]
        return FakeTensor(
            (batch, seq, self.vocab_size),
            argmax_token=FAKE_NEXT_TOKEN,
        )


class FakeInner:
    """The `.model` attribute on an mlx-lm wrapper."""

    def __init__(
        self,
        n_layers: int = DEFAULT_LAYERS,
        hidden_dim: int = HIDDEN_DIM,
        vocab_size: int = VOCAB_SIZE,
    ):
        self.layers = [FakeLayer() for _ in range(n_layers)]
        self.embed_tokens = FakeEmbed(hidden_dim, vocab_size)
        self.norm = lambda x: x


class FakeArgs:
    tie_word_embeddings = True


class FakeModel:
    def __init__(self, n_layers: int = DEFAULT_LAYERS):
        self.model = FakeInner(n_layers)
        self.args = FakeArgs()


class FakeTokenizer:
    """Toy tokenizer: each character becomes one token, with role-marker
    sentinel tokens between turns. Enough to make `apply_chat_template`
    produce reasonably-different lengths across turns so multi-turn tests
    can assert on prefill size shrinkage."""

    USER_TOK = 91
    ASSISTANT_TOK = 92
    ASSISTANT_START = 93

    def __init__(self):
        self.eos_token_id = EOS_TOKEN

    def apply_chat_template(self, history, add_generation_prompt: bool = True):
        ids: list[int] = []
        for m in history:
            role = m.get("role", "")
            if role == "user":
                ids.append(self.USER_TOK)
            elif role == "assistant":
                ids.append(self.ASSISTANT_TOK)
            for ch in m.get("content", ""):
                ids.append(ord(ch) % 80 + 1)  # tokens 1..80 for content
        if add_generation_prompt:
            ids.append(self.ASSISTANT_START)
        return ids

    def decode(self, tokens) -> str:
        if isinstance(tokens, int):
            tokens = [tokens]
        return "".join(chr(t + 32) if 0 < t < 80 else f"[{t}]" for t in tokens)

    def convert_tokens_to_ids(self, marker: str):
        # We don't model any of the special end-of-turn markers; the only
        # stop token is EOS via eos_token_id.
        return None


def fake_load(_model_id: str):
    """Drop-in for `mlx_lm.load`."""
    return FakeModel(), FakeTokenizer()


def fake_make_prompt_cache(model: FakeModel):
    return [FakeKVCache() for _ in range(len(model.model.layers))]


def fake_create_attention_mask(_hidden, _cache_slice):
    return None


def fake_array(data):
    return FakeTensor.from_nested(data)


def fake_argmax_token(logits_row: FakeTensor) -> int:
    if logits_row.argmax_token is None:
        raise AssertionError("fake logits row has no argmax token")
    return logits_row.argmax_token

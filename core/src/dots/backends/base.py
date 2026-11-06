"""Backend runtime interface.

Backends own model primitives: loading weights, cache state, forward
segments, and logits projection. Generation strategies own token policy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..placement.work_units import ModelGeometry

ProgressCallback = Callable[[int, int], Awaitable[None]]
# Runs a hidden state through the rest of the model's layers, wherever they are: `await f(hidden, keep_last=n)`.
# `keep_last` asks for only the last n positions back (a prefill needs one), so the last Mac sends less.
ForwardHidden = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class BackendCapabilities:
    name: str
    model_formats: frozenset[str] = frozenset()
    supports_full_model: bool = True
    supports_partial_layers: bool = False
    supports_pipeline_segments: bool = False


@dataclass(frozen=True)
class ModelAssignment:
    layer_start: int = 0
    layer_end: int | None = None
    needs_head: bool = True

    def key(self, model_id: str) -> tuple[str, int, int | None, bool]:
        return (model_id, self.layer_start, self.layer_end, self.needs_head)


@dataclass(frozen=True)
class PreparedModel:
    """Backend-owned runnable model artifact.

    `source_id` is the user's model id/path. `runtime_id` is the artifact
    the backend will actually load after resolving/downloading/converting.
    """

    source_id: str
    runtime_id: str
    format: str


@dataclass(frozen=True)
class ModelMetadata:
    source_id: str
    runtime_id: str
    geometry: ModelGeometry


@dataclass(frozen=True)
class PreparationRequirement:
    """Resources a backend needs before it can load a model."""

    needs_conversion: bool
    disk_gb: float = 0.0
    ram_gb: float = 0.0
    cpu_utilization: float = 0.0
    io_read_gbps: float = 0.0
    io_write_gbps: float = 0.0
    reason: str = ""


@dataclass
class PrefillOutput:
    logits_row: Any | None
    prefill_tokens: int
    prefill_ms: float
    # The whole conversation as tokens, when the backend has them: speculative decoding looks for its next
    # words in it before asking a draft model
    context_ids: list[int] | None = None


class BackendRuntime(ABC):
    capabilities: BackendCapabilities

    @abstractmethod
    async def load(
        self,
        model_id: str,
        assignment: ModelAssignment,
        progress_callback: ProgressCallback | None = None,
    ) -> None: ...

    @abstractmethod
    def validate_model(self, model_id: str, assignment: ModelAssignment) -> None: ...

    @abstractmethod
    async def prepare_model(
        self, model_id: str, assignment: ModelAssignment
    ) -> PreparedModel: ...

    @abstractmethod
    async def preparation_requirement(
        self,
        model_id: str,
        assignment: ModelAssignment,
    ) -> PreparationRequirement: ...

    @abstractmethod
    async def metadata_for(self, model_id: str) -> ModelMetadata: ...

    @abstractmethod
    def reset_conversation(self, conv_id: str) -> None: ...

    @abstractmethod
    async def prefill(
        self,
        conv_id: str,
        history: list[dict],
        *,
        forward_hidden: ForwardHidden | None = None,
        template_kwargs: dict | None = None,
        keep_rows: int = 1,
    ) -> PrefillOutput:
        """Feeds the conversation's new tokens. `template_kwargs` go to the model's chat template
        (`enable_thinking`, say). `keep_rows` of the prompt's last hidden states are kept for a draft that
        reads them; one picks the first token."""

    @abstractmethod
    async def decode_one(
        self,
        conv_id: str,
        token: int,
        *,
        forward_hidden: ForwardHidden | None = None,
    ) -> Any: ...

    # ---- speculative decoding --------------------------------------------
    # A backend that can verify several tokens in one pass and roll its cache back supports a draft
    # model. The defaults say it can't, so a backend opts in by overriding all three.

    @property
    def supports_speculation(self) -> bool:
        return False

    async def decode_many(
        self,
        conv_id: str,
        tokens: list[int],
        *,
        forward_hidden: ForwardHidden | None = None,
    ) -> Any:
        """Feeds `tokens` in one pass and returns the logits after each, one row per token."""
        raise NotImplementedError(f"{type(self).__name__} can't verify several tokens at once")

    def trim(self, conv_id: str, n: int) -> None:
        """Drops the last `n` tokens from the conversation's cache, as if they had never been fed."""
        raise NotImplementedError(f"{type(self).__name__} can't roll its cache back")

    async def propose(self, conv_id: str, token: int, k: int, recent: list[int] = (),
                      penalty: float = 1.0) -> list[int]:
        """As a draft model: its k most likely next tokens after `token`, each fed back in, greedily, with
        the big model's repetition `penalty` on the `recent` tokens and its own guesses.

        The cache ends holding `token` and all but the last guess. A backend can do this faster than
        k separate steps, for instance without stopping to read each guess back.
        """
        guesses: list[int] = []
        fed = token
        for _ in range(k):
            row = await self.decode_one(conv_id, fed)
            guesses.append(self.sample_token(row, 0.0, recent_tokens=[*recent, *guesses], repetition_penalty=penalty))
            fed = guesses[-1]
        return guesses

    @abstractmethod
    def forward_segment(
        self, conv_id: str, hidden: Any, start: int, end: int
    ) -> Any: ...

    @abstractmethod
    def project_logits(self, hidden_last: Any) -> Any: ...

    @abstractmethod
    def decode_token(self, token: int) -> str: ...

    @abstractmethod
    def decode_tokens(self, tokens: list[int]) -> str: ...

    @abstractmethod
    def sample_token(
        self,
        logits_row: Any,
        temperature: float,
        recent_tokens: list[int] | None = None,
        repetition_penalty: float = 1.0,
    ) -> int: ...

    @abstractmethod
    def encode_hidden(self, hidden: Any) -> dict[str, Any]: ...

    @abstractmethod
    def decode_hidden(self, payload: dict[str, Any]) -> Any: ...

    @abstractmethod
    def stop_ids(self) -> set[int]: ...

    @property
    @abstractmethod
    def active_params(self) -> int: ...

    @property
    @abstractmethod
    def n_layers(self) -> int: ...

    @property
    @abstractmethod
    def geometry(self) -> ModelGeometry | None: ...

    async def unload(self) -> None:
        """Release all model weights and caches this runtime holds so the
        host can reclaim the RAM. Default is a no-op; backends that
        allocate device/host memory override it (the hub calls this when
        it evicts an idle model deployment to make room for another)."""
        return None

    def new_detokenizer(self) -> "StreamDetokenizer":
        """A fresh streaming detokenizer for one generation. Default
        decodes each token alone (correct for single-byte text but
        splits multi-byte UTF-8 across token boundaries); backends with a
        real streaming detokenizer override this."""
        return _PerTokenDetokenizer(self)


class StreamDetokenizer:
    """Turns a token stream into text incrementally, emitting only
    complete UTF-8 segments. One instance per generation."""

    def add_token(self, token: int) -> str:
        """Feed one token; return any newly *completed* text (may be
        empty while a multi-byte character is still being assembled)."""
        raise NotImplementedError

    def finalize(self) -> str:
        """Flush any buffered tail once generation ends."""
        return ""


class _PerTokenDetokenizer(StreamDetokenizer):
    """Fallback that decodes each token in isolation via the runtime."""

    def __init__(self, runtime: "BackendRuntime"):
        self._runtime = runtime

    def add_token(self, token: int) -> str:
        return self._runtime.decode_token(token)

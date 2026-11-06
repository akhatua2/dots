"""Generation strategy interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..backends.base import BackendRuntime, ForwardHidden

TokenCallback = Callable[[str], Awaitable[None]]
# Drops the last n tokens from the caches of the pool's other Macs (see speculative decoding).
TrimRemote = Callable[[int], Awaitable[None]]


@dataclass
class GenerationRequest:
    runtime: "BackendRuntime"
    conv_id: str
    history: list[dict]
    max_tokens: int = 256
    temperature: float = 0.7
    on_token: TokenCallback | None = None
    # Text a draft model has proposed but the model hasn't confirmed yet, replacing the last proposal; "" clears it.
    # Only speculative decoding has drafts. It is for showing, never part of the answer.
    on_draft: TokenCallback | None = None
    forward_hidden: "ForwardHidden | None" = None
    trim_remote: TrimRemote | None = None
    # Penalize tokens seen in the last `repetition_context` generated
    # tokens by dividing their logit (HF-style). 1.0 disables it. Small
    # quantized models loop hard without this; ~1.3 breaks the loops
    # without noticeably hurting coherence.
    repetition_penalty: float = 1.3
    repetition_context: int = 64


@dataclass
class GenerationResult:
    text: str
    stats: dict[str, Any]


class GenerationStrategy(ABC):
    name: str

    @abstractmethod
    async def generate(self, request: GenerationRequest) -> GenerationResult: ...

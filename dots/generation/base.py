"""Generation strategy interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..backends.base import BackendRuntime, ForwardHidden

TokenCallback = Callable[[str], Awaitable[None]]


@dataclass
class GenerationRequest:
    runtime: "BackendRuntime"
    conv_id: str
    history: list[dict]
    max_tokens: int = 256
    temperature: float = 0.7
    on_token: TokenCallback | None = None
    forward_hidden: "ForwardHidden | None" = None


@dataclass
class GenerationResult:
    text: str
    stats: dict[str, Any]


class GenerationStrategy(ABC):
    name: str

    @abstractmethod
    async def generate(self, request: GenerationRequest) -> GenerationResult: ...

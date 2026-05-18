"""ComputePlane abstract base + shared value types."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class VirtualResources:
    """The composed view the plane offers the application.

    Available totals AFTER the plane has reserved what it needs for
    its own routing/state. Used by application code for things like
    deciding max_tokens or whether a context fits.
    """
    ram_gb_available: float
    compute_tflops_per_sec: float
    backends: frozenset[str]
    n_workers: int


@dataclass
class GenerationStats:
    prefill_tokens: int = 0
    decode_tokens: int = 0
    elapsed_ms: float = 0.0
    placement_summary: str = ""


class ComputePlane(ABC):
    """The plane the model code talks to. Workers + strategies live below."""

    @property
    @abstractmethod
    def virtual_resources(self) -> VirtualResources: ...

    @abstractmethod
    async def generate(
        self,
        history: list[dict],
        *,
        max_tokens: int = 256,
        temperature: float = 0.7,
        on_token=None,
    ) -> tuple[str, GenerationStats]:
        """Run one chat turn; stream tokens through `on_token` if provided."""

    @abstractmethod
    async def reset_conversation(self, conv_id: str) -> None: ...

    @abstractmethod
    async def shutdown(self) -> None: ...

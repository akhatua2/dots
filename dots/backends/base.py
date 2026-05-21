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
ForwardHidden = Callable[[Any], Awaitable[Any]]


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
    ) -> PrefillOutput: ...

    @abstractmethod
    async def decode_one(
        self,
        conv_id: str,
        token: int,
        *,
        forward_hidden: ForwardHidden | None = None,
    ) -> Any: ...

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
    def sample_token(self, logits_row: Any, temperature: float) -> int: ...

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

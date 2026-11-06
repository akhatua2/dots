"""llama.cpp backend scaffold.

This backend is the intended CPU path. It is intentionally import-safe on
machines without llama.cpp installed. Runtime execution is not implemented
yet, but the registry can select it and fail clearly at load time.
"""

from __future__ import annotations

from typing import Any

from .base import (
    BackendCapabilities,
    BackendRuntime,
    ForwardHidden,
    ModelAssignment,
    ModelMetadata,
    PrefillOutput,
    PreparationRequirement,
    PreparedModel,
    ProgressCallback,
)


class LlamaCppBackendRuntime(BackendRuntime):
    capabilities = BackendCapabilities(
        name="llama_cpp",
        model_formats=frozenset({"gguf"}),
        supports_full_model=False,
        supports_partial_layers=False,
        supports_pipeline_segments=False,
    )

    async def load(
        self,
        model_id: str,
        assignment: ModelAssignment,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        raise RuntimeError("llama.cpp backend execution is not implemented yet")

    def validate_model(self, model_id: str, assignment: ModelAssignment) -> None:
        if not model_id:
            raise ValueError("model_id is required")

    async def prepare_model(
        self,
        model_id: str,
        assignment: ModelAssignment,
    ) -> PreparedModel:
        self.validate_model(model_id, assignment)
        return PreparedModel(source_id=model_id, runtime_id=model_id, format="gguf")

    async def preparation_requirement(
        self,
        model_id: str,
        assignment: ModelAssignment,
    ) -> PreparationRequirement:
        self.validate_model(model_id, assignment)
        return PreparationRequirement(
            needs_conversion=False,
            reason="llama.cpp backend scaffold",
        )

    async def metadata_for(self, model_id: str) -> ModelMetadata:
        raise RuntimeError(
            "llama.cpp backend metadata resolution is not implemented yet"
        )

    def reset_conversation(self, conv_id: str) -> None:
        raise RuntimeError("llama.cpp backend execution is not implemented yet")

    async def prefill(
        self,
        conv_id: str,
        history: list[dict],
        *,
        forward_hidden: ForwardHidden | None = None,
        template_kwargs: dict | None = None,
    ) -> PrefillOutput:
        raise RuntimeError("llama.cpp backend execution is not implemented yet")

    async def decode_one(
        self,
        conv_id: str,
        token: int,
        *,
        forward_hidden: ForwardHidden | None = None,
    ) -> Any:
        raise RuntimeError("llama.cpp backend execution is not implemented yet")

    def forward_segment(self, conv_id: str, hidden: Any, start: int, end: int) -> Any:
        raise RuntimeError("llama.cpp backend execution is not implemented yet")

    def project_logits(self, hidden_last: Any) -> Any:
        raise RuntimeError("llama.cpp backend execution is not implemented yet")

    def decode_token(self, token: int) -> str:
        raise RuntimeError("llama.cpp backend execution is not implemented yet")

    def decode_tokens(self, tokens: list[int]) -> str:
        raise RuntimeError("llama.cpp backend execution is not implemented yet")

    def sample_token(
        self,
        logits_row: Any,
        temperature: float,
        recent_tokens: list[int] | None = None,
        repetition_penalty: float = 1.0,
    ) -> int:
        raise RuntimeError("llama.cpp backend execution is not implemented yet")

    def encode_hidden(self, hidden: Any) -> dict[str, Any]:
        raise RuntimeError("llama.cpp backend execution is not implemented yet")

    def decode_hidden(self, payload: dict[str, Any]) -> Any:
        raise RuntimeError("llama.cpp backend execution is not implemented yet")

    def stop_ids(self) -> set[int]:
        raise RuntimeError("llama.cpp backend execution is not implemented yet")

    @property
    def active_params(self) -> int:
        return 0

    @property
    def n_layers(self) -> int:
        return 0

    @property
    def geometry(self):
        return None

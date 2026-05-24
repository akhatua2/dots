"""Backend runtime interfaces and implementations."""

from .artifacts import BackendArtifactProfile
from .base import (
    BackendCapabilities,
    BackendRuntime,
    ModelAssignment,
    ModelMetadata,
    PreparationRequirement,
    PreparedModel,
)
from .registry import get_backend_runtime

__all__ = [
    "BackendCapabilities",
    "BackendArtifactProfile",
    "BackendRuntime",
    "ModelAssignment",
    "ModelMetadata",
    "PreparationRequirement",
    "PreparedModel",
    "get_backend_runtime",
]

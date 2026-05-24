"""Backend artifact resource profiles.

Production backends discover these values from local model files, model
metadata, backend conversion plans, or cache state. Offline simulations can
instantiate the same profile from pinned snapshots without duplicating the
resource math.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..placement.work_units import BYTES_PER_GB


@dataclass(frozen=True)
class BackendArtifactProfile:
    """Backend-specific model artifact resource facts."""

    model_id: str
    backend: str
    source_bytes: int
    prepared_bytes: int
    scratch_bytes: int
    resident_ram_bytes: int
    wire_dtype_bytes: int
    metadata_source: str
    source_is_prepared: bool = False

    def __post_init__(self) -> None:
        if not self.model_id:
            raise ValueError("model_id is required")
        if not self.backend:
            raise ValueError("backend is required")
        for field_name in (
            "source_bytes",
            "prepared_bytes",
            "scratch_bytes",
            "resident_ram_bytes",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if self.wire_dtype_bytes <= 0:
            raise ValueError("wire_dtype_bytes must be positive")
        if not self.metadata_source:
            raise ValueError("metadata_source is required")

    @property
    def source_gb(self) -> float:
        return self.source_bytes / BYTES_PER_GB

    @property
    def prepared_gb(self) -> float:
        return self.prepared_bytes / BYTES_PER_GB

    @property
    def scratch_gb(self) -> float:
        return self.scratch_bytes / BYTES_PER_GB

    @property
    def resident_ram_gb(self) -> float:
        return self.resident_ram_bytes / BYTES_PER_GB

    def prepared_cache_gb(self) -> float:
        if self.source_is_prepared:
            return self.source_gb
        return self.prepared_gb

    def disk_gb_for_load(
        self,
        *,
        has_source: bool,
        has_prepared: bool,
    ) -> float:
        """Additional disk required before this artifact can be loaded."""
        if self.source_is_prepared:
            return 0.0 if has_source or has_prepared else self.source_gb

        required = 0.0
        if not has_source:
            required += self.source_gb
        if not has_prepared:
            required += self.prepared_gb + self.scratch_gb
        return required

    def weight_read_gb_per_token(
        self,
        *,
        total_params: int,
        active_params: int,
    ) -> float:
        if total_params <= 0:
            raise ValueError("total_params must be positive")
        if not (0 < active_params <= total_params):
            raise ValueError("active_params must be in (0, total_params]")
        return self.resident_ram_gb * active_params / total_params

    def hidden_state_gb_per_token(self, *, hidden_size: int) -> float:
        if hidden_size <= 0:
            raise ValueError("hidden_size must be positive")
        return hidden_size * self.wire_dtype_bytes / BYTES_PER_GB


def gb_to_bytes(gb: float) -> int:
    if gb < 0:
        raise ValueError("gb must be non-negative")
    return int(round(gb * BYTES_PER_GB))

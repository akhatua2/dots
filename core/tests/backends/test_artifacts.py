from __future__ import annotations

import pytest

from dots.backends.artifacts import BackendArtifactProfile, gb_to_bytes


def test_backend_artifact_profile_models_source_ready_artifact_disk():
    artifact = BackendArtifactProfile(
        model_id="org/model",
        backend="mlx",
        source_bytes=gb_to_bytes(4.0),
        prepared_bytes=gb_to_bytes(4.0),
        scratch_bytes=0,
        resident_ram_bytes=gb_to_bytes(4.0),
        wire_dtype_bytes=2,
        metadata_source="test",
        source_is_prepared=True,
    )

    assert artifact.disk_gb_for_load(has_source=False, has_prepared=False) == 4.0
    assert artifact.disk_gb_for_load(has_source=True, has_prepared=False) == 0.0
    assert artifact.prepared_cache_gb() == 4.0


def test_backend_artifact_profile_models_conversion_disk():
    artifact = BackendArtifactProfile(
        model_id="org/model",
        backend="mlx",
        source_bytes=gb_to_bytes(8.0),
        prepared_bytes=gb_to_bytes(4.0),
        scratch_bytes=gb_to_bytes(2.0),
        resident_ram_bytes=gb_to_bytes(4.0),
        wire_dtype_bytes=2,
        metadata_source="test",
    )

    assert artifact.disk_gb_for_load(has_source=False, has_prepared=False) == 14.0
    assert artifact.disk_gb_for_load(has_source=True, has_prepared=False) == 6.0
    assert artifact.disk_gb_for_load(has_source=True, has_prepared=True) == 0.0
    assert artifact.prepared_cache_gb() == 4.0


def test_backend_artifact_profile_token_transfer_sizes_are_validated():
    artifact = BackendArtifactProfile(
        model_id="org/moe",
        backend="mlx",
        source_bytes=gb_to_bytes(40.0),
        prepared_bytes=gb_to_bytes(40.0),
        scratch_bytes=0,
        resident_ram_bytes=gb_to_bytes(40.0),
        wire_dtype_bytes=2,
        metadata_source="test",
        source_is_prepared=True,
    )

    assert (
        artifact.weight_read_gb_per_token(
            total_params=40_000_000_000,
            active_params=10_000_000_000,
        )
        == 10.0
    )
    assert artifact.hidden_state_gb_per_token(hidden_size=4096) == 8192 / 1_000_000_000

    with pytest.raises(ValueError, match="total_params"):
        artifact.weight_read_gb_per_token(total_params=0, active_params=1)
    with pytest.raises(ValueError, match="active_params"):
        artifact.weight_read_gb_per_token(total_params=10, active_params=11)

from __future__ import annotations

import pytest

from dots.backends.base import ModelAssignment, ModelMetadata
from dots.backends.mlx.runtime import MlxBackendRuntime
from tests.helpers.models import exact_geometry


@pytest.mark.asyncio
async def test_mlx_preparation_requirement_uses_exact_disk_and_assignment_ram(
    monkeypatch,
):
    runtime = MlxBackendRuntime()
    geometry = exact_geometry(
        n_layers=4,
        layer_ram_gb=1.5,
        embed_ram_gb=0.25,
        lm_head_ram_gb=0.5,
        tie_word_embeddings=False,
    )

    async def metadata_for(_model_id):
        return ModelMetadata(
            source_id="org/model",
            runtime_id="org/model",
            geometry=geometry,
        )

    monkeypatch.setattr(runtime, "metadata_for", metadata_for)
    monkeypatch.setattr(
        "dots.backends.mlx.runtime.safetensors_disk_size_gb",
        lambda _model_id: 12.0,
    )

    req = await runtime.preparation_requirement(
        "org/model",
        ModelAssignment(layer_start=1, layer_end=3, needs_head=True),
    )

    assert not req.needs_conversion
    assert req.disk_gb == 12.0
    assert req.ram_gb == 3.75


@pytest.mark.asyncio
async def test_mlx_preparation_requirement_counts_non_head_slice(monkeypatch):
    runtime = MlxBackendRuntime()
    geometry = exact_geometry(n_layers=4, layer_ram_gb=2.0)

    async def metadata_for(_model_id):
        return ModelMetadata(
            source_id="org/model",
            runtime_id="org/model",
            geometry=geometry,
        )

    monkeypatch.setattr(runtime, "metadata_for", metadata_for)
    monkeypatch.setattr(
        "dots.backends.mlx.runtime.safetensors_disk_size_gb",
        lambda _model_id: 12.0,
    )

    req = await runtime.preparation_requirement(
        "org/model",
        ModelAssignment(layer_start=2, layer_end=4, needs_head=False),
    )

    assert req.ram_gb == 4.0

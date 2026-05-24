from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from dots.backends.base import ModelAssignment
from dots.placement.model_metadata import (
    ModelMetadataError,
    assignment_ram_gb,
    geometry_from_config,
    geometry_with_local_safetensors_metadata,
    safetensors_disk_size_gb,
)
from dots.placement.work_units import (
    BYTES_PER_GB,
    DEFAULT_FLOAT16_BYTES_PER_PARAM,
    DEFAULT_QUANTIZED_BYTES_PER_PARAM,
    ModelGeometry,
)


def _write_safetensors_header(
    path: Path,
    tensors: dict[str, tuple[int, int]],
) -> None:
    header = {
        name: {
            "dtype": "F16",
            "shape": [end - start],
            "data_offsets": [start, end],
        }
        for name, (start, end) in tensors.items()
    }
    raw_header = json.dumps(header).encode("utf-8")
    data_len = max(end for _start, end in tensors.values())
    path.write_bytes(struct.pack("<Q", len(raw_header)) + raw_header + b"\0" * data_len)


def test_geometry_from_config_uses_quantization_metadata():
    geom = geometry_from_config(
        {
            "num_hidden_layers": 2,
            "hidden_size": 128,
            "vocab_size": 1000,
            "quantization_config": {"bits": 4},
        }
    )
    assert geom is not None
    assert geom.layer_bytes_per_param == DEFAULT_QUANTIZED_BYTES_PER_PARAM
    assert geom.head_bytes_per_param == DEFAULT_FLOAT16_BYTES_PER_PARAM


def test_geometry_from_config_uses_float16_for_unquantized_model():
    geom = geometry_from_config(
        {
            "text_config": {
                "num_hidden_layers": 2,
                "hidden_size": 128,
                "vocab_size": 1000,
            }
        }
    )
    assert geom is not None
    assert geom.layer_bytes_per_param == DEFAULT_FLOAT16_BYTES_PER_PARAM


def test_local_safetensors_metadata_attaches_exact_layer_and_head_bytes(tmp_path):
    _write_safetensors_header(
        tmp_path / "model.safetensors",
        {
            "model.embed_tokens.weight": (0, 100),
            "model.layers.0.self_attn.q_proj.weight": (100, 130),
            "model.layers.1.mlp.down_proj.weight": (130, 170),
            "model.norm.weight": (170, 180),
            "rope.freqs": (180, 185),
        },
    )
    base = ModelGeometry(
        n_layers=2,
        hidden_size=8,
        vocab_size=32,
        tie_word_embeddings=True,
    )

    geom = geometry_with_local_safetensors_metadata(tmp_path, base)

    assert geom.layer_ram_gb == (30 / BYTES_PER_GB, 40 / BYTES_PER_GB)
    assert geom.embed_ram_gb == 100 / BYTES_PER_GB
    assert geom.lm_head_ram_gb is None
    assert geom.head_extra_ram_gb == 15 / BYTES_PER_GB
    assert geom.metadata_source == "local_safetensors"


def test_local_safetensors_metadata_requires_every_decoder_layer(tmp_path):
    _write_safetensors_header(
        tmp_path / "model.safetensors",
        {
            "model.embed_tokens.weight": (0, 100),
            "model.layers.0.self_attn.q_proj.weight": (100, 130),
            "lm_head.weight": (130, 160),
        },
    )
    base = ModelGeometry(n_layers=2, hidden_size=8, vocab_size=32)

    with pytest.raises(ModelMetadataError, match="missing decoder layer bytes"):
        geometry_with_local_safetensors_metadata(tmp_path, base)


def test_assignment_ram_uses_only_assigned_slice_plus_head():
    geom = ModelGeometry(
        n_layers=3,
        hidden_size=8,
        vocab_size=32,
        layer_ram_gb=(1.0, 2.0, 3.0),
        embed_ram_gb=0.25,
        lm_head_ram_gb=0.5,
        head_extra_ram_gb=0.125,
    )

    head = assignment_ram_gb(geom, ModelAssignment(0, 2, True))
    non_head = assignment_ram_gb(geom, ModelAssignment(2, 3, False))

    assert head == 3.875
    assert non_head == 3.0


def test_assignment_ram_requires_exact_metadata():
    geom = ModelGeometry(n_layers=2, hidden_size=8, vocab_size=32)
    with pytest.raises(ModelMetadataError, match="exact decoder layer RAM"):
        assignment_ram_gb(geom, ModelAssignment(0, 2, True))


def test_local_safetensors_disk_size_uses_real_file_size(tmp_path):
    path = tmp_path / "model.safetensors"
    _write_safetensors_header(path, {"model.layers.0.weight": (0, 10)})

    assert safetensors_disk_size_gb(tmp_path) == path.stat().st_size / BYTES_PER_GB


def test_remote_safetensors_disk_size_requires_file_sizes(monkeypatch):
    class _Sibling:
        def __init__(self, name, size):
            self.rfilename = name
            self.size = size

    class _Info:
        siblings = [
            _Sibling("config.json", 10),
            _Sibling("model-00001.safetensors", 100),
            _Sibling("model-00002.safetensors", 200),
        ]

    class _Api:
        def repo_info(self, *_args, **_kwargs):
            return _Info()

    monkeypatch.setattr("huggingface_hub.HfApi", _Api)

    assert safetensors_disk_size_gb("org/model") == 300 / BYTES_PER_GB

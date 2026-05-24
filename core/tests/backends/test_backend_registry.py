from __future__ import annotations

import pytest

from dots.backends.registry import (
    available_backend_names,
    get_backend_runtime,
    is_backend_available,
    supported_backend_names,
)


def test_workers_advertise_only_implemented_backends():
    assert supported_backend_names() == frozenset({"mlx"})


def test_available_backend_names_probe_runtime(monkeypatch):
    monkeypatch.setattr(
        "dots.backends.registry.is_backend_available",
        lambda name: name == "mlx",
    )
    assert available_backend_names() == frozenset({"mlx"})


def test_unavailable_backend_is_not_advertised(monkeypatch):
    monkeypatch.setattr(
        "dots.backends.registry.is_backend_available",
        lambda _name: False,
    )
    assert available_backend_names() == frozenset()


def test_backend_availability_rejects_unknown_backend():
    assert not is_backend_available("llama_cpp")


def test_backend_name_is_required():
    with pytest.raises(RuntimeError, match="backend name is required"):
        get_backend_runtime("")


def test_unknown_backend_fails_clearly():
    with pytest.raises(RuntimeError, match="backend not implemented"):
        get_backend_runtime("not-a-backend")


def test_llama_cpp_scaffold_is_not_advertised_but_importable():
    runtime = get_backend_runtime("llama_cpp")
    assert runtime.capabilities.name == "llama_cpp"

"""Dots-native placement schema.

This package should describe work in backend-neutral terms. Backends then
decide whether they can realize a model/assignment pair and how to encode
their own tensors on the wire.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelComponent:
    """Backend-neutral model component for placement.

    `kind` examples: embedding, attention_block, mamba_block,
    moe_expert_group, norm, lm_head, sampler. The scheduler can place
    these without knowing the framework that executes them.
    """

    component_id: str
    kind: str
    ram_gb: float
    flops_per_token: float
    order: int
    metadata: dict[str, str] | None = None


@dataclass(frozen=True)
class HiddenStatePayload:
    """Opaque backend-owned hidden state bytes plus descriptive metadata."""

    backend: str
    encoding: str
    shape: tuple[int, ...]
    dtype: str
    data: bytes

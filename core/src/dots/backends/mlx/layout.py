"""Where a model keeps its parts, however mlx-lm nests them.

Plain models keep the embedding, decoder layers and final norm at `model.model`; newer ones that can also
take images nest them under `model.language_model.model` (Qwen3.5), and some name the final norm
differently (LFM2's `embedding_norm`). Running a slice of the layers, or loading only a slice, goes
through these instead of assuming one layout.
"""

from __future__ import annotations

from ...placement.tensor_names import HEAD_TENSOR, LAYER_TENSOR, VISION_TENSOR  # noqa: F401  (re-exported)

FINAL_NORM_NAMES = ("norm", "embedding_norm", "final_layernorm")


def text_model(model):
    """The module holding `embed_tokens`, the decoder `layers` and the final norm."""
    node = model
    for _ in range(4):
        if hasattr(node, "embed_tokens") and isinstance(getattr(node, "layers", None), list):
            return node
        node = getattr(node, "language_model", None) or getattr(node, "model", None)
        if node is None:
            break
    raise ValueError(f"can't find the decoder layers in {type(model).__name__}")


def final_norm_name(text) -> str:
    for name in FINAL_NORM_NAMES:
        if hasattr(text, name):
            return name
    raise ValueError(f"can't find the final norm in {type(text).__name__}")


def output_owner(model):
    """The module that would hold `lm_head`: the text wrapper when there is one."""
    return getattr(model, "language_model", model)

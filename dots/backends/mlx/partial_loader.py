"""Load only a layer range of an mlx-lm model into RAM.

Disk is cheap, RAM is the constraint. This module mirrors what
`mlx_lm.utils.load_model` does — pick the per-architecture model class
from `config.json`, construct an empty model, run mlx-lm's quantization
pass, load weights with `model.load_weights` — except it filters the
weight tensors to *only* those that belong to the assigned layer range
(plus, optionally, the head tensors: embed / final norm / lm_head).

Unowned modules are replaced with `_DroppedModule` stubs so MLX releases
their tensors. `nn.quantize` skips dropped modules because they don't
have a `.to_quantized` method.

The full-load case (`layer_start=0, layer_end=n_layers, needs_head=True`)
must produce a model that's bit-identical to `mlx_lm.load(model_id)`.
That's the regression test that catches sanitize/quantize/tying bugs
before partial placements rely on them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import mlx.core as mx
import mlx.nn as nn
from mlx_lm.utils import (
    _download,
    _get_classes,
    load_config,
    load_tokenizer,
)

log = logging.getLogger("dots.partial_loader")


class _DroppedModule(nn.Module):
    """Stand-in for a Module that this worker doesn't own.

    Holds no parameters so the underlying tensors are released as soon as
    we overwrite the original module reference. Calling it raises — that
    way a programming error (worker trying to use a module outside its
    assignment) surfaces immediately instead of producing silent garbage.
    """

    def __call__(self, *_args, **_kwargs):
        raise RuntimeError(
            "tried to invoke a Module not owned by this partial-load worker"
        )


# Top-level tensors that belong to the "head" of the model (embed +
# final norm + lm_head, with their quantization sidecars). Any tensor
# starting with "model.layers." is per-layer and handled separately.
_HEAD_TENSOR_PREFIXES: tuple[str, ...] = (
    "model.embed_tokens.",
    "model.norm.",
    "lm_head.",
)


def _is_owned(name: str, layer_start: int, layer_end: int, needs_head: bool) -> bool:
    """Decide whether tensor `name` belongs on this worker."""
    if name.startswith("model.layers."):
        # "model.layers.{i}.<rest>"
        parts = name.split(".", 3)
        if len(parts) < 3:
            return False
        try:
            i = int(parts[2])
        except ValueError:
            return False
        return layer_start <= i < layer_end
    if any(name.startswith(p) for p in _HEAD_TENSOR_PREFIXES):
        return needs_head
    # Anything else (rare top-level tensors like rope buffers) — be
    # liberal and keep it. Models occasionally stash small things at the
    # root that every worker needs.
    return True


def _drop_unowned_modules(
    model: nn.Module,
    layer_start: int,
    layer_end: int,
    needs_head: bool,
) -> None:
    """Replace every Module outside our assignment with a `_DroppedModule`
    stub. Must run *before* `nn.quantize` so the quantizer skips them
    (its class_predicate checks `f"{path}.scales" in weights`, and we've
    filtered weights to exclude unowned tensors)."""
    inner = getattr(model, "model", None)
    if inner is not None and hasattr(inner, "layers"):
        for i in range(len(inner.layers)):
            if not (layer_start <= i < layer_end):
                inner.layers[i] = _DroppedModule()
    if not needs_head:
        if inner is not None:
            for attr in ("embed_tokens", "norm"):
                if hasattr(inner, attr):
                    try:
                        setattr(inner, attr, _DroppedModule())
                    except (AttributeError, TypeError):
                        pass
        if hasattr(model, "lm_head") and getattr(model, "lm_head") is not None:
            try:
                model.lm_head = _DroppedModule()
            except (AttributeError, TypeError):
                pass


def _filter_weights(
    raw: dict[str, mx.array],
    layer_start: int,
    layer_end: int,
    needs_head: bool,
) -> dict[str, mx.array]:
    return {
        name: arr
        for name, arr in raw.items()
        if _is_owned(name, layer_start, layer_end, needs_head)
    }


def load_partial(
    model_id: str,
    *,
    layer_start: int = 0,
    layer_end: int | None = None,
    needs_head: bool = True,
) -> tuple[nn.Module, Any, int]:
    """Load an mlx-lm model with only the specified layer range materialized.

    Returns `(model, tokenizer, n_layers)`. `n_layers` is read from the
    config so callers know the full layer count without inspecting weights.

    Notes:
      * `layer_end=None` means "to the last layer" (full model).
      * `needs_head=True` is required exactly on the head worker — it
        owns the embedding table, the final norm, and the lm_head.
      * The full-load case (range [0, n_layers) + needs_head=True) is the
        regression-test pivot; it must match `mlx_lm.load` output.
    """
    import glob

    model_path = Path(_download(model_id))
    config = load_config(model_path)

    # Mirror mlx_lm.utils.load_model's quantization-config hoist.
    if "quantization_config" not in config:
        text_config = config.get("text_config", {})
        if "quantization_config" in text_config:
            config["quantization_config"] = text_config["quantization_config"]

    model_class, model_args_class = _get_classes(config=config)
    model_args = model_args_class.from_dict(config)
    model = model_class(model_args)
    n_layers = len(model.model.layers)
    end = layer_end if layer_end is not None else n_layers
    if not (0 <= layer_start <= end <= n_layers):
        raise ValueError(
            f"invalid layer range [{layer_start}, {end}) for {n_layers}-layer model"
        )

    # Lazy-load all weight files but only keep tensors we own.
    weight_files = sorted(glob.glob(str(model_path / "model*.safetensors")))
    if not weight_files:
        raise FileNotFoundError(f"no safetensors found in {model_path}")
    weights: dict[str, mx.array] = {}
    for wf in weight_files:
        shard = mx.load(wf)
        # `mx.load` returns a dict of name → array. Iterating and
        # filtering means only the owned tensors are retained as MLX
        # arrays; the rest are released when `shard` goes out of scope.
        for name, arr in shard.items():
            if _is_owned(name, layer_start, end, needs_head):
                weights[name] = arr

    # Some model classes rewrite key names before load (e.g. fused QKV).
    if hasattr(model, "sanitize"):
        weights = model.sanitize(weights)

    # Drop modules BEFORE quantize. `nn.quantize`'s class_predicate
    # checks `f"{path}.scales" in weights`; with the filtered dict, the
    # dropped modules have no scales and get skipped automatically.
    _drop_unowned_modules(model, layer_start, end, needs_head)

    quantization = config.get("quantization")
    if quantization is not None:

        def class_predicate(path: str, m: nn.Module) -> bool:
            if path in config["quantization"]:
                return config["quantization"][path]
            if not hasattr(m, "to_quantized"):
                return False
            return f"{path}.scales" in weights

        nn.quantize(
            model,
            group_size=quantization["group_size"],
            bits=quantization["bits"],
            mode=quantization.get("mode", "affine"),
            class_predicate=class_predicate,
        )

    model.eval()
    # strict=False because owned modules' weights are present, but the
    # dropped modules contribute no expected weights — strict would fail.
    model.load_weights(list(weights.items()), strict=False)
    mx.eval(model.parameters())

    tokenizer = load_tokenizer(model_path)
    return model, tokenizer, n_layers

"""What each tensor in a checkpoint is, by name, whatever nesting the model uses.

Plain checkpoints name the decoder `model.layers.N...`; ones whose model can also take images put the text
model under `language_model.` (Qwen3.5), and keep the vision encoder beside it. Placement estimates memory
and workers load their share through these, so neither assumes one layout.
"""

from __future__ import annotations

import re

# "model.layers.12.mlp..." or "language_model.model.layers.12.mlp..."
LAYER_TENSOR = re.compile(r"^(?:language_model\.)?model\.layers\.(\d+)\.")
EMBED_TENSOR = re.compile(r"^(?:language_model\.)?model\.embed_tokens\.")
FINAL_NORM_TENSOR = re.compile(r"^(?:language_model\.)?model\.(?:norm|embedding_norm|final_layernorm)\.")
OUTPUT_TENSOR = re.compile(r"^(?:language_model\.)?lm_head\.")
# The embedding, final norm and output layer, with their quantization sidecars: the head Mac's
HEAD_TENSOR = re.compile(f"{EMBED_TENSOR.pattern}|{FINAL_NORM_TENSOR.pattern}|{OUTPUT_TENSOR.pattern}")
# A vision encoder, unused when serving text
VISION_TENSOR = re.compile(r"^(?:vision_tower|visual|vision_model|multi_modal_projector|mm_projector)\.")


def layer_index(name: str) -> int | None:
    match = LAYER_TENSOR.match(name)
    return int(match.group(1)) if match else None

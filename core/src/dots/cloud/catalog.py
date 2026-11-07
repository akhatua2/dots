"""What models a pool can run, and what answers cost. The apps read both from the cloud.

The ladder is the cloud's list of models by the memory a pool needs to run them. The model the hub
actually serves is added to the bottom of it, described from what the hub reports, so a pool's model
is always one the hub can run.
"""

from __future__ import annotations

import re

# id, name, GB a pool needs, parameters, layers, active parameters (B), tokens a second nearby, credits an answer
# The newest open models a pool can run, by the memory they need pooled: (id, name, min GB, params,
# layers, active billions a token, rough tokens a second, credits a prompt). Ids are the MLX builds a
# hub loads. Every architecture here runs split across Macs (core/tests/backends/test_mlx_architectures.py);
# the speeds are estimates until pools measure them.
_LADDER = [
    ("mlx-community/Qwen3.5-0.8B-4bit", "Qwen3.5 0.8B", 1, "0.8B", 24, 0.8, 90, 0),
    ("mlx-community/Qwen3.5-2B-MLX-4bit", "Qwen3.5 2B", 2, "2B", 24, 2, 70, 0),
    ("mlx-community/Qwen3.5-4B-MLX-4bit", "Qwen3.5 4B", 4, "4B", 32, 4, 50, 0),
    ("mlx-community/LFM2.5-8B-A1B-MLX-4bit", "LFM2.5 8B", 6, "8B", 24, 1.5, 60, 1),
    ("mlx-community/Qwen3.5-9B-MLX-4bit", "Qwen3.5 9B", 7, "9B", 32, 9, 30, 1),
    ("mlx-community/gpt-oss-20b-MXFP4-Q4", "gpt-oss 20B", 13, "20B", 24, 3.6, 40, 1),
    ("mlx-community/Qwen3.8-27B-4bit", "Qwen3.8 27B", 19, "27B", 64, 27, 14, 1),
    ("mlx-community/Qwen3.6-35B-A3B-4bit", "Qwen3.6 35B", 24, "35B", 40, 3, 35, 1),
    ("mlx-community/gpt-oss-120b-MXFP4-Q4", "gpt-oss 120B", 70, "120B", 36, 5.1, 25, 2),
    ("mlx-community/MiniMax-M2.7-4bit", "MiniMax M2.7", 150, "230B", 62, 10, 18, 2),
    ("mlx-community/GLM-5.3-4bit", "GLM 5.3", 480, "744B", 78, 40, 9, 3),
    ("mlx-community/Kimi-K2.5", "Kimi K2.5", 720, "1T", 61, 32, 8, 4),
]
_SPECIAL: list[tuple] = []
_LONG = {"T": "trillion", "B": "billion", "M": "million"}

# Anthropic's list prices in USD per million tokens, and what a credit is worth.
TERMS = {
    "creditUSD": 0.001,
    "discount": 0.3,
    "typicalInput": 1500,
    "typicalOutput": 600,
    "prices": {
        "claude-opus-5-5": {"input": 4, "output": 20},
        "claude-sonnet-5-5": {"input": 2, "output": 10},
        "claude-haiku-5-5": {"input": 0.1, "output": 0.5},
        "claude-fable-5-1": {"input": 10, "output": 50},
    },
}


def _tier(entry: tuple) -> dict:
    model_id, name, min_gb, params, layers, active_b, tok_per_sec, credits = entry
    number, unit = params[:-1], params[-1]
    return {
        "id": model_id,
        "name": name,
        "minGB": min_gb,
        "params": params,
        "paramsLong": f"{number} {_LONG.get(unit, '')} parameters".replace("  ", " "),
        "tokPerSec": tok_per_sec,
        "credits": credits,
        "layers": layers,
        "activeB": active_b,
    }


def short_name(model_id: str) -> str:
    """'mlx-community/Qwen3.5-2B-MLX-4bit' -> 'Qwen3.5 2B'; 'mlx-community/gpt-oss-20b-MXFP4-Q4' -> 'gpt-oss 20b'."""
    tail = model_id.split("/")[-1]
    tail = re.sub(r"-(Instruct|Chat|it|\d+bit|MLX|mlx|mflux|GGUF|MXFP4|mxfp4|[Qq]\d|DQ\w*|OptiQ)\b", "", tail)
    return re.sub(r"-(?=[0-9A-Z])", " ", tail).strip()  # a hyphen before a size or a version is a space


def served_tier(model_id: str, layers: int, tok_per_sec: float) -> dict:
    """The model the hub serves, as a rung of the ladder. It needs only a little memory, so it sits first."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*([BbMm])\b", model_id)
    size = float(match.group(1)) if match else 0.0
    unit = match.group(2).upper() if match else "B"
    billions = size if unit == "B" else size / 1000
    params = f"{match.group(1)}{unit}" if match else "?"
    return {
        "id": model_id,
        "name": short_name(model_id),
        "minGB": max(1, round(billions * 0.6)),  # 4-bit weights, with room for the cache
        "params": params,
        "paramsLong": f"{match.group(1)} {_LONG[unit]} parameters" if match else "unknown size",
        "tokPerSec": round(tok_per_sec) if tok_per_sec else 0,
        "credits": 0,
        "layers": layers,
        "activeB": billions,
        "readsImages": reads_images(model_id),
    }


def reads_images(model_id: str) -> bool:
    """Whether a model reads images, from its own config: one with a vision encoder does."""
    import json

    from huggingface_hub import hf_hub_download

    try:
        with open(hf_hub_download(model_id, "config.json")) as f:
            config = json.load(f)
    except Exception:
        return False
    return "vision_config" in config and "image_token_id" in config


def ladder(served: dict | None) -> dict:
    """{tiers, special}: the ladder, with the hub's own model first when it serves one."""
    tiers = [_tier(t) for t in _LADDER]
    if served is not None:
        listed = next((t for t in tiers if t["id"] == served["id"]), None)
        if listed is None:
            tiers.insert(0, served)
        elif served["tokPerSec"]:
            listed["tokPerSec"] = served["tokPerSec"]  # what the pool measured beats the estimate
    return {"tiers": tiers, "special": [_tier(t) for t in _SPECIAL]}


def best_for(tiers: list[dict], gb: int) -> dict | None:
    fits = [t for t in tiers if t["minGB"] <= gb]
    return fits[-1] if fits else None


def gateway_credits(model_id: str, input_tokens: int, output_tokens: int) -> tuple[int, int]:
    """(credits charged, credits at list price) for a frontier answer: list price less the discount."""
    price = TERMS["prices"].get(model_id)
    if price is None:
        return 0, 0
    usd = (input_tokens * price["input"] + output_tokens * price["output"]) / 1e6
    list_credits = max(1, -(-usd // TERMS["creditUSD"]))
    charged = max(1, -(-(usd * (1 - TERMS["discount"])) // TERMS["creditUSD"]))
    return int(charged), int(list_credits)

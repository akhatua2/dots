"""What models a pool can run, and what answers cost. The apps read both from the cloud.

The ladder is the cloud's list of models by the memory a pool needs to run them. The model the hub
actually serves is added to the bottom of it, described from what the hub reports, so a pool's model
is always one the hub can run.
"""

from __future__ import annotations

import re

# id, name, GB a pool needs, parameters, layers, active parameters (B), tokens a second nearby, credits an answer
_LADDER = [
    ("llama-3.1-8b", "Llama 3.1 8B", 6, "8B", 32, 8, 62, 1),
    ("gemma-3-27b", "Gemma 3 27B", 18, "27B", 62, 27, 38, 1),
    ("llama-3.3-70b", "Llama 3.3 70B", 40, "70B", 80, 70, 19, 1),
    ("qwen3-235b", "Qwen3 235B", 136, "235B", 94, 22, 24, 2),
    ("deepseek-v3.1", "DeepSeek V3.1", 396, "671B", 61, 37, 11, 3),
    ("kimi-k2", "Kimi K2", 620, "1T", 61, 32, 9, 4),
]
_SPECIAL = [("qwen3-coder-480b", "Qwen3 Coder 480B", 270, "480B", 62, 35, 13, 3)]

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
    """'mlx-community/Qwen2.5-0.5B-Instruct-4bit' -> 'Qwen2.5 0.5B'."""
    tail = model_id.split("/")[-1]
    tail = re.sub(r"-(Instruct|Chat|\d+bit|MLX|mlx|GGUF)\b", "", tail)
    return tail.replace("-", " ").strip()


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
    }


def ladder(served: dict | None) -> dict:
    """{tiers, special}: the ladder, with the hub's own model first when it serves one."""
    tiers = [_tier(t) for t in _LADDER]
    if served is not None and all(t["id"] != served["id"] for t in tiers):
        tiers.insert(0, served)
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

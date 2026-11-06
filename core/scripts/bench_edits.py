"""Editing a picture again and again: does each edit change only what it was asked to, and do earlier edits last?

A chain of small, local edits runs on one picture, each on the last one's result, twice: as the model returns
them, and with `keep_unchanged`, which keeps the original pixels wherever the edit didn't mean to change
anything. For each step:

- Kept elsewhere: how close the picture stays to the one before, outside the place the edit was about
  (PSNR in dB, higher is closer; 40+ is indistinguishable, under 25 visibly changed).
- Drift: the same against the very first picture, outside every place edited so far: what accumulates.
- Done: whether a vision model (Qwen3.5, read through dots) sees each edit so far in the picture.

    uv run python scripts/bench_edits.py --start nook.png --out /tmp/edits
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from bench_images import tool_for
from dots.images import ImageJob, keep_unchanged

# A chain is a JSON list of steps: {"edit": instruction, "box": [x0, y0, x1, y1] where it applies, in the start
# picture's pixels, "check": a yes-or-no question, "expect": "yes" or "no"}. Chains live in scripts/edit_chains/.
CHAINS = Path(__file__).parent / "edit_chains"
MARGIN = 12  # pixels around each place that count as part of it


def pixels(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.float32)


def psnr_outside(a: np.ndarray, b: np.ndarray, boxes: list[tuple[int, int, int, int]]) -> float:
    if a.shape != b.shape:
        b = np.asarray(Image.fromarray(b.astype(np.uint8)).resize(a.shape[1::-1]), dtype=np.float32)
    keep = np.ones(a.shape[:2], dtype=bool)
    for x0, y0, x1, y1 in boxes:
        keep[max(0, y0 - MARGIN):y1 + MARGIN, max(0, x0 - MARGIN):x1 + MARGIN] = False
    mse = float(((a - b) ** 2).mean(axis=2)[keep].mean())
    return 99.0 if mse == 0 else 10 * np.log10(255**2 / mse)


async def edit(tool, picture: bytes, instruction: str, seed: int, folder: Path) -> tuple[bytes, float]:
    source, out = folder / "source.png", folder / "out" / "image.png"
    source.write_bytes(picture)
    out.parent.mkdir(exist_ok=True)
    for old in out.parent.glob("*.png"):
        old.unlink()
    started = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(*tool.arguments(ImageJob(prompt=instruction, seed=seed), out, source),
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    log = (await proc.communicate())[0].decode(errors="replace")
    drawn = sorted(out.parent.glob("*.png"))
    if proc.returncode != 0 or not drawn:
        raise RuntimeError(log.strip().splitlines()[-1])
    return drawn[0].read_bytes(), time.perf_counter() - started


async def judge(repo: str, pictures: dict[str, bytes], chain: list[dict]) -> dict[str, list[str]]:
    """For each picture, the vision model's yes or no to every check in the chain."""
    import base64

    from mlx_lm import load

    from dots.backends.mlx.runtime import FullModelHost, MlxBackendRuntime
    from dots.generation.autoregressive import AutoregressiveGeneration
    from dots.generation.base import GenerationRequest

    model, tokenizer = load(repo)
    runtime = MlxBackendRuntime.from_host(FullModelHost(model, tokenizer), source=repo)
    answers = {}
    for name, data in pictures.items():
        url = "data:image/png;base64," + base64.b64encode(data).decode()
        said = []
        for i, question in enumerate(step["check"] for step in chain):
            history = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                                    {"type": "text", "text": question + " Answer yes or no."}]}]
            runtime.reset_conversation(f"{name}-{i}")
            result = await AutoregressiveGeneration().generate(GenerationRequest(
                runtime=runtime, conv_id=f"{name}-{i}", history=history, max_tokens=4, temperature=0.0,
                repetition_penalty=1.0, template_kwargs={"enable_thinking": False}))
            said.append("yes" if result.text.strip().lower().startswith("yes") else "no")
        answers[name] = said
    return answers


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", type=Path, required=True)
    p.add_argument("--chain", default="nook", help="a chain in scripts/edit_chains, by name")
    p.add_argument("--model", default="mflux-community/flux-3-image-klein-mflux-q4")
    p.add_argument("--judge", default="mlx-community/Qwen3.5-4B-MLX-4bit")
    p.add_argument("--seed", type=int, default=5)
    p.add_argument("--out", type=Path, default=Path("bench_edits"))
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    tool = tool_for(args.model)
    chain = json.loads((CHAINS / f"{args.chain}.json").read_text())
    start = args.start.read_bytes()
    first = pixels(start)
    rows, pictures = [], {"start": start}
    for mode in ("as returned", "keep unchanged"):
        picture, edited = start, []
        for step, item in enumerate(chain, 1):
            instruction, box = item["edit"], tuple(item["box"])
            raw, seconds = await edit(tool, picture, instruction, args.seed, args.out)
            result = keep_unchanged(picture, raw) if mode == "keep unchanged" else raw
            edited.append(box)
            kept = psnr_outside(pixels(picture), pixels(result), [box])
            drift = psnr_outside(first, pixels(result), edited)
            name = f"{mode.split()[0]}-{step}"
            (args.out / f"{name}.png").write_bytes(result)
            pictures[name] = result
            rows.append({"mode": mode, "step": step, "kept_db": round(kept, 1), "drift_db": round(drift, 1),
                         "seconds": round(seconds)})
            print(f"{mode:<15} step {step}  kept elsewhere {kept:5.1f} dB  drift from start {drift:5.1f} dB  {seconds:4.0f} s",
                  flush=True)
            picture = result
    answers = await judge(args.judge, pictures, chain)
    expected = [item["expect"] for item in chain]
    for row in rows:
        said = answers[f"{row['mode'].split()[0]}-{row['step']}"]
        row["done"] = sum(s == w for s, w in zip(said[:row["step"]], expected[:row["step"]]))
        print(f"{row['mode']:<15} step {row['step']}  edits seen {row['done']}/{row['step']}  ({' '.join(said[:row['step']])})")
    (args.out / "results.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    asyncio.run(main())

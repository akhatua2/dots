"""Image models on this Mac: how long a picture takes and how much memory it needs, drawn the way a worker draws.

Each picture is a fresh run of the image tool, as a worker's job is, so the time includes loading the model.
Pictures are saved next to the results, to look at.

    uv run python scripts/bench_images.py --out /tmp/images
    uv run python scripts/bench_images.py --models mflux-community/flux-3-image-klein-mflux-q4 --sizes 1024
"""

from __future__ import annotations

import argparse
import asyncio
import re
import time
from pathlib import Path

from dots.images import ImageJob, ImageTool

PROMPTS = {
    "fox": "A small red fox sitting in fresh snow, morning light, photograph",
    "sign": "A hand-painted wooden sign on a cafe door that says 'OPEN 7 DAYS', warm afternoon light",
    "poster": "A minimalist travel poster of Lisbon with yellow trams, flat illustration",
}
EDIT = "Make it a snowy winter evening, keep the road and the mountains"

# How each model family is drawn and edited with mflux 0.24 (None: it has no edit tool; it restyles instead)
FAMILIES = {
    "flux-3-image-klein": ("mflux-generate-flux3", "mflux-generate-flux3-edit", "flux3-klein"),
    "flux2-klein-4b": ("mflux-generate-flux2", "mflux-generate-flux2-edit", "flux2-klein-4b"),
    "Z-Image-Turbo": ("mflux-generate-z-image-turbo", None, "z-image-turbo"),
}
MFLUX = "uvx --from mflux==0.24.0 "


def tool_for(model: str) -> ImageTool:
    for key, (draw, edit, base) in FAMILIES.items():
        if key.lower() in model.lower():
            return ImageTool(model=model, command=tuple((MFLUX + draw).split()),
                             edit_command=tuple((MFLUX + edit).split()) if edit else None, base_model=base, timeout_s=1800)
    raise SystemExit(f"don't know how to run {model}")


async def run(tool: ImageTool, job: ImageJob, out: Path) -> tuple[float, float | None]:
    """Seconds and peak GB for one picture, saved at `out`."""
    source = None
    if job.image is not None:
        source = out.with_suffix(".source.png")
        source.write_bytes(job.image)
    started = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(*tool.arguments(job, out, source), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    log = (await proc.communicate())[0].decode(errors="replace")
    seconds = time.perf_counter() - started
    if proc.returncode != 0:
        raise RuntimeError(log.strip().splitlines()[-1])
    peak = re.search(r"Peak MLX memory: ([\d.]+) GB", log)
    return seconds, float(peak.group(1)) if peak else None


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--models", nargs="+", default=["mflux-community/flux-3-image-klein-mflux-q4"])
    p.add_argument("--sizes", type=int, nargs="+", default=[512, 1024])
    p.add_argument("--prompts", nargs="+", default=list(PROMPTS), choices=list(PROMPTS))
    p.add_argument("--edit", type=Path, help="also edit this picture with each model")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--steps", type=int, help="denoising steps; by default each model's own")
    p.add_argument("--out", type=Path, default=Path("bench_images"))
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"{'model':<46} {'picture':<10} {'size':>5} {'seconds':>8} {'peak GB':>8}")
    for model in args.models:
        tool = tool_for(model)
        name = model.split("/")[-1]
        jobs = [(f"{prompt}", size, ImageJob(prompt=PROMPTS[prompt], width=size, height=size, seed=args.seed,
                                                   steps=args.steps))
                for size in args.sizes for prompt in args.prompts]
        if args.edit:
            jobs.append(("edit", 0, ImageJob(prompt=EDIT, image=args.edit.read_bytes(), strength=0.6, seed=args.seed)))
        for label, size, job in jobs:
            steps = f"-{args.steps}steps" if args.steps else ""
            out = args.out / f"{name}-{label}-{size or 'source'}{steps}.png"
            try:
                seconds, peak = await run(tool, job, out)
            except RuntimeError as e:
                print(f"{name:<46} {label:<10} {size or '':>5}  failed: {e}")
                continue
            print(f"{name:<46} {label:<10} {size or 'same':>5} {seconds:>8.1f} {peak if peak is not None else '?':>8}",
                  flush=True)


if __name__ == "__main__":
    asyncio.run(main())

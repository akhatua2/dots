"""Draws or edits one picture with FLUX 3 Image Klein through mflux, showing the picture as it forms.

It runs in mflux's own environment (`uvx --from mflux python -I flux3_draw.py ...`), not in dots', so it
imports only mflux, MLX and Pillow. It takes the arguments of mflux's own commands that dots uses.

mflux's own step-by-step pictures show the noisy picture in the middle of the drawing: static until the
last few steps. This shows instead what the model expects the finished picture to be after each step, which
is recognizable from the first: the scheduler takes Euler steps on a flow from noise (at s = 1) to the
picture (at s = 0), so a step's direction v = (x' - x) / (s' - s), and the picture it heads for is x - s v.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import mlx.core as mx
import numpy as np
from PIL import Image


class _Previews:
    """After each step, the picture the model is heading for, saved as `seed_N_stepKofM.png` in `folder`."""

    def __init__(self, model, folder: Path, steps: int, height: int, width: int):
        from mflux.models.common.vae.tiling_config import TilingConfig
        from mflux.models.flux3.latent_creator.flux3_latent_creator import Flux3LatentCreator
        from mflux.models.flux3.model.flux3_scheduler.flux3_flow_map_scheduler import Flux3FlowMapScheduler

        self.model, self.folder = model, folder
        self.decode_tokens = Flux3LatentCreator.decode_tokens
        self.grid = Flux3LatentCreator.latent_shape(height, width)
        self.levels = Flux3FlowMapScheduler(num_inference_steps=steps).timesteps
        self.steps = steps
        self.last = None
        # A preview each step adds up: decode it in tiles, and give the memory back after each one
        self.tiles = TilingConfig()
        folder.mkdir(parents=True, exist_ok=True)

    def call_before_loop(self, seed, prompt, latents, config, **_):
        self.last = latents

    def call_in_loop(self, t, seed, prompt, latents, config, time_steps):
        before, after = self.levels[t].item(), self.levels[t + 1].item()
        direction = (latents - self.last) / (after - before)
        heading = self.last - before * direction
        self.last = latents
        pixels = self.decode_tokens(self.model.vae, heading, *self.grid, tiling_config=self.tiles)
        mx.eval(pixels)
        _save(pixels, self.folder / f"seed_{seed}_step{t + 1}of{self.steps}.png")
        del pixels, heading, direction
        mx.clear_cache()


def _save(pixels: mx.array, path: Path) -> None:
    """A decoded picture (1, 3, H, W) in [-1, 1], as a PNG."""
    array = np.array(pixels[0].astype(mx.float32)).transpose(1, 2, 0)
    Image.fromarray(((array.clip(-1, 1) + 1) * 127.5).round().astype(np.uint8)).save(path)


def _model_path(model: str) -> str:
    if Path(model).exists():
        return model
    from huggingface_hub import snapshot_download

    return snapshot_download(model)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--base-model")  # mflux's commands take it; this one only draws FLUX 3 Image Klein
    p.add_argument("--prompt", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--steps", type=int, default=8)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--image-paths", nargs="+", type=Path, help="pictures to edit, or to draw from")
    p.add_argument("--stepwise-image-output-dir", type=Path)
    args = p.parse_args()

    from mflux.models.common.config.model_config import ModelConfig

    config = ModelConfig.flux3_image_klein()
    if args.image_paths:
        from mflux.models.flux3.variants.edit.flux3_klein_edit import Flux3KleinEdit

        model = Flux3KleinEdit(model_path=_model_path(args.model), model_config=config)
        size = Image.open(args.image_paths[0]).size
    else:
        from mflux.models.flux3.variants.txt2img.flux3_klein import Flux3Klein

        model = Flux3Klein(model_path=_model_path(args.model), model_config=config)
        size = (1024, 1024)
    width, height = args.width or size[0], args.height or size[1]
    if args.stepwise_image_output_dir:
        model.callbacks.register(_Previews(model, args.stepwise_image_output_dir, args.steps, height, width))
    kwargs = dict(seed=args.seed, prompt=args.prompt, num_inference_steps=args.steps, height=height, width=width)
    if args.image_paths:
        kwargs["image_paths"] = [str(path) for path in args.image_paths]
    picture = model.generate_image(**kwargs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    picture.image.save(args.output)


if __name__ == "__main__":
    main()

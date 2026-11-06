"""Making and editing images: a whole job on one Mac.

A diffusion model runs every layer many times over one image, so splitting it across Macs would cross the
network at every step; and the image models people use fit on one Mac. So the hub sends each image job,
whole, to a worker that offers an image model, and that Mac draws it.

The model runs in its own tool, mflux by default, which uv installs on first use in an environment of its
own: it brings torch and opencv, which the worker itself never needs. A worker offers images only when
`DOTS_IMAGE_MODEL` is set.

    DOTS_IMAGE_MODEL         the model it offers; unset: none
    DOTS_IMAGE_COMMAND       the tool that draws with it
    DOTS_IMAGE_EDIT_COMMAND  the tool that edits pictures with it; unset: the drawing tool starts from the
                             picture instead (image to image), which restyles but doesn't follow instructions
    DOTS_IMAGE_BASE_MODEL    the family a converted model belongs to, as the tool names it
    DOTS_IMAGE_STEPS         denoising steps when a job doesn't say (8: FLUX 3 Klein's own default is 16,
                             and 8 drew nearly the same pictures in 44 s instead of 81 s)
    DOTS_IMAGE_TIMEOUT_S     how long one image may take

The defaults suit FLUX 3 Image Klein (mflux-community/flux-3-image-klein-mflux-q4, 5.3 GB): in November
2026 it was second among open image models on Artificial Analysis's text-to-image board (Elo 1,043, one
behind Qwen-Image-2.1 at twice the size and a research-only license), among the top three for editing,
and it edits by instruction from up to ten reference pictures. Its license (FLUX Kommunity) is free for
commercial use below $5M a year in revenue. FLUX.2 klein 4B is the Apache-licensed choice, a step lower.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_COMMAND = "uvx --from mflux==0.24.0 mflux-generate-flux3"
DEFAULT_EDIT_COMMAND = "uvx --from mflux==0.24.0 mflux-generate-flux3-edit"
DEFAULT_BASE_MODEL = "flux3-klein"
# Sides are multiples of this, as the models' latents need
SIDE_STEP = 16
LARGEST_SIDE = 2048


@dataclass
class ImageJob:
    """One picture to draw: from words, or from a starting picture (`image`) the words describe a change to."""

    prompt: str
    # None: the tool's default, or the starting picture's size
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    seed: int | None = None
    image: bytes | None = None
    # How much of the starting picture to keep, 0 to 1
    strength: float | None = None

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("say what to draw")
        for side in (self.width, self.height):
            if side is not None and (not SIDE_STEP <= side <= LARGEST_SIDE or side % SIDE_STEP):
                raise ValueError(f"sides must be multiples of {SIDE_STEP} up to {LARGEST_SIDE}")
        if self.strength is not None and not 0 <= self.strength <= 1:
            raise ValueError("strength is between 0 and 1")


def parse_size(size: str | None) -> tuple[int | None, int | None]:
    """'768x512' -> (768, 512), as OpenAI's image API writes sizes; none given, (None, None)."""
    if not size or size == "auto":
        return None, None
    try:
        width, height = (int(n) for n in size.lower().split("x"))
    except ValueError as e:
        raise ValueError("size is WIDTHxHEIGHT, like 1024x1024") from e
    return width, height


def _steps(value: str) -> int | None:
    return int(value) if value.strip() else None


@dataclass(frozen=True)
class ImageTool:
    model: str
    command: tuple[str, ...]
    edit_command: tuple[str, ...] | None
    base_model: str | None
    timeout_s: float
    steps: int | None = None

    @classmethod
    def from_env(cls) -> "ImageTool | None":
        model = os.environ.get("DOTS_IMAGE_MODEL", "").strip()
        if not model:
            return None
        edit_command = os.environ.get("DOTS_IMAGE_EDIT_COMMAND", DEFAULT_EDIT_COMMAND).strip()
        return cls(model=model,
                   command=tuple(shlex.split(os.environ.get("DOTS_IMAGE_COMMAND", DEFAULT_COMMAND))),
                   edit_command=tuple(shlex.split(edit_command)) if edit_command else None,
                   base_model=os.environ.get("DOTS_IMAGE_BASE_MODEL", DEFAULT_BASE_MODEL).strip() or None,
                   timeout_s=float(os.environ.get("DOTS_IMAGE_TIMEOUT_S", "900")),
                   steps=_steps(os.environ.get("DOTS_IMAGE_STEPS", "8")))

    def arguments(self, job: ImageJob, out: Path, source: Path | None) -> list[str]:
        editing = source is not None and self.edit_command is not None
        args = [*(self.edit_command if editing else self.command), "--model", self.model, "--prompt", job.prompt,
                "--output", str(out)]
        if job.width is not None and job.height is not None:
            args += ["--width", str(job.width), "--height", str(job.height)]
        if self.base_model:
            args += ["--base-model", self.base_model]
        steps = job.steps if job.steps is not None else self.steps
        if steps is not None:
            args += ["--steps", str(steps)]
        if job.seed is not None:
            args += ["--seed", str(job.seed)]
        if editing:
            args += ["--image-paths", str(source)]
        elif source is not None:
            args += ["--image-path", str(source)]
            if job.strength is not None:
                args += ["--image-strength", str(job.strength)]
        return args

    async def draw(self, job: ImageJob) -> tuple[bytes, float]:
        """The picture as PNG bytes, and how long it took in ms."""
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="dots-image-") as folder:
            folder_path = Path(folder)
            source = None
            if job.image is not None:
                source = folder_path / "source"
                source.write_bytes(job.image)
            out = folder_path / "out" / "image.png"
            out.parent.mkdir()
            proc = await asyncio.create_subprocess_exec(*self.arguments(job, out, source),
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            try:
                log, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise RuntimeError(f"the picture took longer than {self.timeout_s:.0f} s") from None
            # The tool doesn't overwrite: it may have added a suffix to the name
            drawn = sorted(out.parent.glob("*.png"))
            if proc.returncode != 0 or not drawn:
                tail = log.decode(errors="replace").strip().splitlines()[-3:]
                raise RuntimeError("the image tool failed: " + " / ".join(tail))
            return drawn[0].read_bytes(), (time.perf_counter() - started) * 1000

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
    DOTS_IMAGE_EDIT_PIXELS   a picture larger than this is edited at this size, and the edit put back into the
                             full-size picture (`keep_unchanged`), so it keeps its definition everywhere else
    DOTS_IMAGE_TIMEOUT_S     how long one image may take

The defaults suit FLUX 3 Image Klein (mflux-community/flux-3-image-klein-mflux-q4, 5.3 GB): in November
2026 it was second among open image models on Artificial Analysis's text-to-image board (Elo 1,043, one
behind Qwen-Image-2.1 at twice the size and a research-only license), among the top three for editing,
and it edits by instruction from up to ten reference pictures. Its license (FLUX Kommunity) is free for
commercial use below $5M a year in revenue. FLUX.2 klein 4B is the Apache-licensed choice, a step lower.
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import shlex
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# FLUX 3 Image Klein runs through dots' own driver of mflux, which shows the picture the model is heading for
# after each step instead of the noisy one (image_tools/flux3_draw.py); it edits when given a picture
_DRIVER = Path(__file__).parent / "image_tools" / "flux3_draw.py"
DEFAULT_COMMAND = f"uvx --from mflux==0.24.0 python -I {shlex.quote(str(_DRIVER))}"
DEFAULT_EDIT_COMMAND = DEFAULT_COMMAND
DEFAULT_BASE_MODEL = "flux3-klein"
# Sides are multiples of this, as the models' latents need
SIDE_STEP = 16
LARGEST_SIDE = 2048
# Previews of the picture forming, one a step, are sent this large at most
PREVIEW_SIDE = 384
# The image as it stands after a step: (step, of how many, a small JPEG)
StepCallback = Callable[[int, int, bytes], Awaitable[None]]


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
    # An edit keeps the starting picture's own pixels wherever it didn't mean to change anything
    keep_unchanged: bool = True

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
    edit_pixels: int = 1024 * 1024

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
                   steps=_steps(os.environ.get("DOTS_IMAGE_STEPS", "8")),
                   edit_pixels=int(os.environ.get("DOTS_IMAGE_EDIT_PIXELS", str(1024 * 1024))))

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

    async def draw(self, job: ImageJob, on_step: StepCallback | None = None) -> tuple[bytes, float]:
        """The picture as PNG bytes, and how long it took in ms. `on_step` gets the picture as it forms, after
        each denoising step, so whoever asked can watch it sharpen instead of waiting on a blank."""
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="dots-image-") as folder:
            folder_path = Path(folder)
            source = None
            if job.image is not None:
                source = folder_path / "source.png"
                source.write_bytes(_at_most(job.image, self.edit_pixels))
            out = folder_path / "out" / "image.png"
            out.parent.mkdir()
            arguments = self.arguments(job, out, source)
            steps_dir = folder_path / "steps"
            if on_step is not None:
                arguments += ["--stepwise-image-output-dir", str(steps_dir)]
            proc = await asyncio.create_subprocess_exec(*arguments, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.STDOUT)
            watching = asyncio.create_task(_watch_steps(steps_dir, on_step)) if on_step is not None else None
            try:
                log, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise RuntimeError(f"the picture took longer than {self.timeout_s:.0f} s") from None
            finally:
                if watching is not None:
                    watching.cancel()
            # The tool doesn't overwrite: it may have added a suffix to the name
            drawn = sorted(out.parent.glob("*.png"))
            if proc.returncode != 0 or not drawn:
                tail = log.decode(errors="replace").strip().splitlines()[-3:]
                raise RuntimeError("the image tool failed: " + " / ".join(tail))
            png = drawn[0].read_bytes()
            if job.image is not None and job.keep_unchanged:
                png = keep_unchanged(job.image, png)
            return png, (time.perf_counter() - started) * 1000


_STEP_FILE = re.compile(r"_step(\d+)of(\d+)\.png$")


async def _watch_steps(folder: Path, on_step: StepCallback) -> None:
    """Sends each step's picture as the tool writes it, oldest first, until cancelled."""
    sent: set[str] = set()
    while True:
        await asyncio.sleep(0.4)
        if not folder.exists():
            continue
        ready = []
        for path in folder.glob("*.png"):
            match = _STEP_FILE.search(path.name)
            if match and path.name not in sent:
                ready.append((int(match.group(1)), int(match.group(2)), path))
        for step, total, path in sorted(ready):
            try:
                preview = _preview(path.read_bytes())
            except (OSError, ValueError):
                continue  # still being written; next look
            sent.add(path.name)
            await on_step(step, total, preview)


def _preview(data: bytes) -> bytes:
    from PIL import Image

    image = Image.open(io.BytesIO(data)).convert("RGB")
    image.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=70)
    return out.getvalue()


def _at_most(data: bytes, pixels: int) -> bytes:
    """The picture as PNG, shrunk to at most `pixels` (upright, as the camera meant it)."""
    from PIL import Image, ImageOps

    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    scale = min(1.0, (pixels / (image.width * image.height)) ** 0.5)
    if scale < 1:
        step = 16  # the models' latents need sides in multiples of this
        size = (max(step, int(image.width * scale) // step * step), max(step, int(image.height * scale) // step * step))
        image = image.resize(size, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def keep_unchanged(original: bytes, edited: bytes, *, threshold: float = 0.08, grow: float = 0.03,
                   largest_share: float = 0.5) -> bytes:
    """`edited`, with the original's own pixels wherever the edit didn't mean to change anything.

    An edit model redraws the whole picture, so even what it leaves alone comes back slightly different:
    colors shift, textures soften, and over several edits the picture wanders off. This finds where the
    edit changed the picture (blurred, so the redraw's fine noise doesn't count), grows that region a little,
    feathers its edge, and takes the edit only there. A change over more than `largest_share` of the picture
    was meant to be global (relighting, a new style), so it is kept whole.
    """
    from PIL import Image, ImageFilter, ImageOps

    before = ImageOps.exif_transpose(Image.open(io.BytesIO(original))).convert("RGB")
    after = Image.open(io.BytesIO(edited)).convert("RGB")
    if after.size != before.size:
        after = after.resize(before.size, Image.Resampling.LANCZOS)
    side = max(before.size)
    soften = ImageFilter.GaussianBlur(max(1, side // 256))
    a = np.asarray(before.filter(soften), dtype=np.float32) / 255
    b = np.asarray(after.filter(soften), dtype=np.float32) / 255
    changed = np.abs(a - b).mean(axis=2) > threshold
    if changed.mean() > largest_share:
        return _png(after)
    if not changed.any():
        return _png(before)
    mask = Image.fromarray((changed * 255).astype(np.uint8))
    # Drop specks, then grow the region and feather its edge
    mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    reach = max(3, int(side * grow) | 1)
    small = mask.resize((max(1, mask.width // 4), max(1, mask.height // 4)), Image.Resampling.BILINEAR)
    small = small.filter(ImageFilter.MaxFilter(max(3, (reach // 4) | 1)))
    mask = small.resize(mask.size, Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(reach / 3))
    weight = (np.asarray(mask, dtype=np.float32) / 255)[..., None]
    out = np.asarray(before, dtype=np.float32) * (1 - weight) + np.asarray(after, dtype=np.float32) * weight
    return _png(Image.fromarray(out.round().clip(0, 255).astype(np.uint8)))


def _png(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()

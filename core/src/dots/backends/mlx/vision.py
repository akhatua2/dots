"""Reading images: a model's vision encoder, run on the head Mac.

Qwen3.5 reads images natively. Its encoder turns an image into one vector per 32x32-pixel square, in the
language model's hidden size, and those take the places of the image's placeholder tokens in the prompt.
From there the prompt's hidden state crosses Macs like any other, so only the head Mac reads images, and
it loads the encoder the first time someone sends one.

Qwen3.5 was trained to give image tokens 3D positions (frame, row, column) where text has one. mlx-lm
runs every position as one sequence, and dots does too: the encoder already places each square in the
image itself. On Qwen3.5 0.8B, questions about a screenshot and a photo, including which way a road
curves and what is in a corner, got the same answers both ways. So images ask nothing new of the Macs
running the later layers.

The encoder follows mlx-vlm's port of Qwen3-VL's (MIT).
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import math
import os
from dataclasses import dataclass, fields

import mlx.core as mx
import mlx.nn as nn
import numpy as np


@dataclass
class VisionConfig:
    depth: int
    hidden_size: int
    num_heads: int
    intermediate_size: int
    out_hidden_size: int
    num_position_embeddings: int
    patch_size: int = 16
    temporal_patch_size: int = 2
    spatial_merge_size: int = 2
    in_channels: int = 3

    @classmethod
    def from_dict(cls, values: dict) -> "VisionConfig":
        return cls(**{f.name: values[f.name] for f in fields(cls) if f.name in values})


def _rotate_half(x):
    half = x.shape[-1] // 2
    return mx.concatenate([-x[..., half:], x[..., :half]], axis=-1)


class _Attention(nn.Module):
    def __init__(self, size: int, heads: int):
        super().__init__()
        self.heads = heads
        self.qkv = nn.Linear(size, 3 * size, bias=True)
        self.proj = nn.Linear(size, size)

    def __call__(self, x, cos, sin):
        n = x.shape[0]
        q, k, v = self.qkv(x).reshape(n, 3, self.heads, -1).transpose(1, 2, 0, 3)  # each (heads, n, head size)
        dtype = q.dtype
        q = (q * cos + _rotate_half(q) * sin).astype(dtype)
        k = (k * cos + _rotate_half(k) * sin).astype(dtype)
        out = mx.fast.scaled_dot_product_attention(q[None], k[None], v[None], scale=q.shape[-1] ** -0.5)
        return self.proj(out[0].transpose(1, 0, 2).reshape(n, -1))


class _MLP(nn.Module):
    def __init__(self, size: int, hidden: int):
        super().__init__()
        self.linear_fc1 = nn.Linear(size, hidden)
        self.linear_fc2 = nn.Linear(hidden, size)

    def __call__(self, x):
        return self.linear_fc2(nn.gelu_approx(self.linear_fc1(x)))


class _Block(nn.Module):
    def __init__(self, cfg: VisionConfig):
        super().__init__()
        self.norm1 = nn.LayerNorm(cfg.hidden_size, eps=1e-6)
        self.norm2 = nn.LayerNorm(cfg.hidden_size, eps=1e-6)
        self.attn = _Attention(cfg.hidden_size, cfg.num_heads)
        self.mlp = _MLP(cfg.hidden_size, cfg.intermediate_size)

    def __call__(self, x, cos, sin):
        x = x + self.attn(self.norm1(x), cos, sin)
        return x + self.mlp(self.norm2(x))


class _PatchEmbed(nn.Module):
    def __init__(self, cfg: VisionConfig):
        super().__init__()
        kernel = (cfg.temporal_patch_size, cfg.patch_size, cfg.patch_size)
        self.proj = nn.Conv3d(cfg.in_channels, cfg.hidden_size, kernel_size=kernel, stride=kernel, bias=True)


class _Merger(nn.Module):
    """Each 2x2 group of squares becomes one token in the language model's hidden size."""

    def __init__(self, cfg: VisionConfig):
        super().__init__()
        merged = cfg.hidden_size * cfg.spatial_merge_size**2
        self.norm = nn.LayerNorm(cfg.hidden_size, eps=1e-6)
        self.linear_fc1 = nn.Linear(merged, merged)
        self.linear_fc2 = nn.Linear(merged, cfg.out_hidden_size)

    def __call__(self, x):
        x = self.norm(x).reshape(-1, self.linear_fc1.weight.shape[1])
        return self.linear_fc2(nn.gelu(self.linear_fc1(x)))


class VisionModel(nn.Module):
    def __init__(self, cfg: VisionConfig):
        super().__init__()
        self.cfg = cfg
        self.patch_embed = _PatchEmbed(cfg)
        self.pos_embed = nn.Embedding(cfg.num_position_embeddings, cfg.hidden_size)
        self.blocks = [_Block(cfg) for _ in range(cfg.depth)]
        self.merger = _Merger(cfg)

    def __call__(self, patches, rows: int, cols: int):
        """One image's patches (from `ImageReader.patches`), `rows` x `cols` squares: one token per 2x2 of them."""
        cfg = self.cfg
        x = patches.reshape(-1, cfg.in_channels, cfg.temporal_patch_size, cfg.patch_size, cfg.patch_size)
        x = self.patch_embed.proj(x.moveaxis(1, 4)).reshape(-1, cfg.hidden_size)
        x = x + self._position_embeddings(rows, cols)
        cos, sin = self._rotations(rows, cols)
        for block in self.blocks:
            x = block(x, cos, sin)
        return self.merger(x)

    def _merge_order(self, grid):
        """A (rows, cols, ...) grid in the order the squares are fed: 2x2 groups, row by row."""
        m = self.cfg.spatial_merge_size
        rows, cols = grid.shape[:2]
        return grid.reshape(rows // m, m, cols // m, m, *grid.shape[2:]).swapaxes(1, 2).reshape(-1, *grid.shape[2:])

    def _position_embeddings(self, rows: int, cols: int):
        """The learned grid of position embeddings, stretched to this image's grid by bilinear interpolation."""
        side = int(self.cfg.num_position_embeddings**0.5)

        def axis(n):
            at = mx.linspace(0, side - 1, n)
            low = at.astype(mx.int32)
            return low, mx.minimum(low + 1, side - 1), at - low.astype(mx.float32)

        (r0, r1, dr), (c0, c1, dc) = axis(rows), axis(cols)
        corners = [(r0, c0, (1 - dr)[:, None] * (1 - dc)[None, :]), (r0, c1, (1 - dr)[:, None] * dc[None, :]),
                   (r1, c0, dr[:, None] * (1 - dc)[None, :]), (r1, c1, dr[:, None] * dc[None, :])]
        total = None
        for r, c, weight in corners:
            part = self.pos_embed(r[:, None] * side + c[None, :])
            part = part * weight.astype(part.dtype)[..., None]
            total = part if total is None else total + part
        return self._merge_order(total)

    def _rotations(self, rows: int, cols: int):
        """Rotary angles from each square's row and column, half the head size each."""
        head = self.cfg.hidden_size // self.cfg.num_heads
        dim = head // 2
        inv_freq = 1.0 / (10000.0 ** (mx.arange(0, dim, 2, dtype=mx.float32) / dim))
        table = mx.outer(mx.arange(max(rows, cols), dtype=mx.float32), inv_freq)
        r = mx.broadcast_to(mx.arange(rows)[:, None], (rows, cols))
        c = mx.broadcast_to(mx.arange(cols)[None, :], (rows, cols))
        angles = mx.concatenate([table[self._merge_order(r)], table[self._merge_order(c)]], axis=-1)
        angles = mx.concatenate([angles, angles], axis=-1)
        return mx.cos(angles), mx.sin(angles)


@dataclass
class ImageReader:
    """Turns an image into what the encoder reads, and says how many tokens it will take in the prompt."""

    cfg: VisionConfig
    mean: tuple[float, ...]
    std: tuple[float, ...]
    min_pixels: int
    max_pixels: int

    def size(self, height: int, width: int) -> tuple[int, int]:
        """The image resized to whole 32-pixel squares, as close to its shape as fits within the pixel limits."""
        step = self.cfg.patch_size * self.cfg.spatial_merge_size
        h, w = max(step, round(height / step) * step), max(step, round(width / step) * step)
        if h * w > self.max_pixels:
            scale = math.sqrt(height * width / self.max_pixels)
            h = max(step, math.floor(height / scale / step) * step)
            w = max(step, math.floor(width / scale / step) * step)
        elif h * w < self.min_pixels:
            scale = math.sqrt(self.min_pixels / (height * width))
            h, w = math.ceil(height * scale / step) * step, math.ceil(width * scale / step) * step
        return h, w

    def patches(self, data: bytes) -> tuple[np.ndarray, int, int]:
        """The image's patches, one row per 16x16 square (repeated over the two frames a video would have),
        and its grid in squares."""
        from PIL import Image, ImageOps

        cfg = self.cfg
        image = ImageOps.exif_transpose(_open(data)).convert("RGB")
        height, width = self.size(image.height, image.width)
        image = image.resize((width, height), Image.Resampling.BICUBIC)
        pixels = (np.asarray(image, dtype=np.float32) / 255.0 - np.array(self.mean, np.float32)) / np.array(self.std, np.float32)
        pixels = np.broadcast_to(pixels.transpose(2, 0, 1)[None], (cfg.temporal_patch_size, 3, height, width))
        p, m = cfg.patch_size, cfg.spatial_merge_size
        rows, cols = height // p, width // p
        grid = pixels.reshape(cfg.temporal_patch_size, 3, rows // m, m, p, cols // m, m, p)
        grid = grid.transpose(2, 5, 3, 6, 1, 0, 4, 7)
        return np.ascontiguousarray(grid.reshape(rows * cols, -1)), rows, cols

    def tokens(self, rows: int, cols: int) -> int:
        return rows * cols // self.cfg.spatial_merge_size**2


@dataclass
class Vision:
    model: VisionModel
    reader: ImageReader
    image_token: int

    def read(self, data: bytes):
        """The image as prompt rows, one per placeholder token."""
        patches, rows, cols = self.reader.patches(data)
        dtype = self.model.patch_embed.proj.weight.dtype
        return self.model(mx.array(patches).astype(dtype), rows, cols)

    def expand(self, ids: list[int], images: list[bytes]) -> tuple[list[int], list[tuple[int, int]]]:
        """`ids` with each image's one placeholder repeated once per row the image takes, and where each
        image's rows are."""
        out: list[int] = []
        spans: list[tuple[int, int]] = []
        for token in ids:
            if token == self.image_token and len(spans) < len(images):
                n = self.tokens_for(images[len(spans)])
                spans.append((len(out), len(out) + n))
                out += [token] * n
            else:
                out.append(token)
        if len(spans) != len(images):
            raise ValueError("the chat template left out some of the images")
        return out, spans

    def place(self, hidden, images: list[tuple[int, bytes]]):
        """`hidden` with each image's rows put in, at the position its placeholders start."""
        parts, at = [], 0
        for start, data in images:
            rows = self.read(data).astype(hidden.dtype)[None]
            parts += [hidden[:, at:start], rows]
            at = start + rows.shape[1]
        parts.append(hidden[:, at:])
        return mx.concatenate(parts, axis=1)

    def tokens_for(self, data: bytes) -> int:
        with _open(data) as image:
            width, height = image.size
            if (image.getexif() or {}).get(0x0112) in (5, 6, 7, 8):  # stored on its side
                width, height = height, width
        h, w = self.reader.size(height, width)
        return self.reader.tokens(h // self.reader.cfg.patch_size, w // self.reader.cfg.patch_size)


def _open(data: bytes):
    from PIL import Image, UnidentifiedImageError

    try:
        return Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError) as e:
        raise ValueError("couldn't read the image: it isn't a picture in a format dots knows") from e


# Names the encoder's tensors have in mlx-community's builds and in the original releases
_PREFIXES = ("vision_tower.", "model.visual.")


def _files_with_encoder(path: str) -> list[str]:
    index = os.path.join(path, "model.safetensors.index.json")
    if not os.path.exists(index):
        return ["model.safetensors"]
    with open(index) as f:
        weight_map = json.load(f)["weight_map"]
    return sorted({file for name, file in weight_map.items() if name.startswith(_PREFIXES)})


def load_vision(source: str, max_pixels: int) -> Vision | None:
    """The encoder of the model at `source` (a repo or a folder), or None when the model doesn't read images.
    A Mac holding only some of the model's layers fetches just the files the encoder is in. `max_pixels` caps
    how large an image is read: a 3200x2000 screenshot is 6229 tokens whole and 1029 at a million pixels,
    with the same answers about it."""
    local = os.path.isdir(source)
    if local:
        path = source
    else:
        from huggingface_hub import snapshot_download

        path = snapshot_download(source, allow_patterns=["*.json"])
    with open(os.path.join(path, "config.json")) as f:
        config = json.load(f)
    if "vision_config" not in config or "image_token_id" not in config:
        return None
    files = _files_with_encoder(path)
    if not local:
        path = snapshot_download(source, allow_patterns=["*.json", *files])
    weights = {}
    for file in files:
        for name, value in mx.load(os.path.join(path, file)).items():
            for prefix in _PREFIXES:
                if name.startswith(prefix):
                    weights[name[len(prefix):]] = value
    if not weights:
        return None
    cfg = VisionConfig.from_dict(config["vision_config"])
    proj = weights["patch_embed.proj.weight"]
    if proj.shape[1] == cfg.in_channels and proj.shape[-1] != cfg.in_channels:  # PyTorch's layout
        weights["patch_embed.proj.weight"] = proj.transpose(0, 2, 3, 4, 1)
    model = VisionModel(cfg)
    model.load_weights(list(weights.items()), strict=True)
    mx.eval(model.parameters())

    pre = {}
    if os.path.exists(os.path.join(path, "preprocessor_config.json")):
        with open(os.path.join(path, "preprocessor_config.json")) as f:
            pre = json.load(f)
    size = pre.get("size", {})
    reader = ImageReader(cfg, tuple(pre.get("image_mean", (0.5, 0.5, 0.5))), tuple(pre.get("image_std", (0.5, 0.5, 0.5))),
                         min_pixels=size.get("shortest_edge", 0),
                         max_pixels=min(max_pixels, size.get("longest_edge", max_pixels)))
    return Vision(model, reader, config["image_token_id"])


def image_bytes(part: dict) -> bytes:
    """The image in an OpenAI-style message part, `{"type": "image_url", "image_url": {"url": "data:..."}}`.
    Only images sent inline are read: a worker never fetches a URL someone else chose."""
    url = (part.get("image_url") or {}).get("url", "") if isinstance(part.get("image_url"), dict) else part.get("image_url", "")
    if not isinstance(url, str) or not url.startswith("data:") or "," not in url:
        raise ValueError("images must be sent inline, as data: URLs")
    header, payload = url.split(",", 1)
    try:
        return base64.b64decode(payload, validate=True) if header.endswith(";base64") else payload.encode()
    except binascii.Error as e:
        raise ValueError("the image's data URL isn't valid base64") from e


def take_images(history: list[dict]) -> tuple[list[dict], list[bytes]]:
    """The conversation with each image part replaced by the chat template's placeholder, and the images,
    in the order they appear."""
    images: list[bytes] = []
    messages = []
    for message in history:
        content = message.get("content")
        if isinstance(content, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in content):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    images.append(image_bytes(part))
                    parts.append({"type": "image"})
                else:
                    parts.append(part)
            message = {**message, "content": parts}
        messages.append(message)
    return messages, images

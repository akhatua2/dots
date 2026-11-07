"""Pictures in a chat, as OpenAI's API sends them: plain Python, so reading a conversation needs no MLX (a hub's
tests run where MLX doesn't). The vision encoder that reads the pictures is in mlx/vision.py."""

from __future__ import annotations

import base64
import binascii


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

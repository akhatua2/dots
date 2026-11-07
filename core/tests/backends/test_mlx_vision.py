"""Reading images on the head Mac: sizing, placeholders, and where the image's rows go in the prompt.

The encoder itself was checked against mlx-vlm's on a real Qwen3.5 0.8B: the same output, to the bit, on
the same pixels, and pixels within 1e-7 of its preprocessing. These pin down the parts around it.
"""

import base64
import io

import pytest

mx = pytest.importorskip("mlx.core")
Image = pytest.importorskip("PIL.Image")

from dots.backends.mlx.vision import (  # noqa: E402
    ImageReader,
    Vision,
    VisionConfig,
    VisionModel,
    image_bytes,
    take_images,
)

IMAGE_TOKEN = 99
CFG = VisionConfig(depth=1, hidden_size=32, num_heads=2, intermediate_size=64, out_hidden_size=16,
                   num_position_embeddings=64)


def _png(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 90)).save(out, format="PNG")
    return out.getvalue()


def _vision(max_pixels: int = 1024 * 1024) -> Vision:
    mx.random.seed(0)
    model = VisionModel(CFG)
    mx.eval(model.parameters())
    return Vision(model, ImageReader(CFG, (0.5,) * 3, (0.5,) * 3, min_pixels=0, max_pixels=max_pixels), IMAGE_TOKEN)


def test_images_are_resized_to_whole_squares_within_the_limit():
    reader = _vision(max_pixels=1024 * 1024).reader
    h, w = reader.size(2000, 3200)  # a Retina screenshot
    assert h % 32 == 0 and w % 32 == 0 and h * w <= 1024 * 1024
    assert abs(w / h - 1.6) < 0.05  # keeps its shape
    assert reader.size(100, 70) == (96, 64)  # small ones round to the nearest square


def test_one_row_per_placeholder():
    vision = _vision()
    data = _png(200, 130)
    patches, rows, cols = vision.reader.patches(data)
    assert patches.shape == (rows * cols, 3 * 2 * 16 * 16)
    out = vision.read(data)
    assert out.shape == (vision.tokens_for(data), CFG.out_hidden_size)


def test_placeholders_expand_and_the_rows_go_where_they_were():
    vision = _vision()
    first, second = _png(64, 64), _png(128, 64)
    ids, spans = vision.expand([1, IMAGE_TOKEN, 2, 3, IMAGE_TOKEN, 4], [first, second])
    n1, n2 = vision.tokens_for(first), vision.tokens_for(second)
    assert spans == [(1, 1 + n1), (3 + n1, 3 + n1 + n2)]
    assert ids == [1, *[IMAGE_TOKEN] * n1, 2, 3, *[IMAGE_TOKEN] * n2, 4]

    hidden = mx.zeros((1, len(ids), CFG.out_hidden_size))
    placed = vision.place(hidden, [(spans[0][0], first), (spans[1][0], second)])
    assert placed.shape == hidden.shape
    assert mx.allclose(placed[0, 1:1 + n1], vision.read(first)).item()
    assert mx.allclose(placed[0, spans[1][0]:spans[1][1]], vision.read(second)).item()
    assert mx.all(placed[0, 0] == 0).item() and mx.all(placed[0, 1 + n1] == 0).item()


def test_messages_give_up_their_images_in_order():
    a, b = _png(32, 32), _png(64, 32)
    url = lambda data: "data:image/png;base64," + base64.b64encode(data).decode()  # noqa: E731
    history = [
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url(a)}}, {"type": "text", "text": "and"}]},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": [{"type": "text", "text": "this"}, {"type": "image_url", "image_url": {"url": url(b)}}]},
    ]
    messages, images = take_images(history)
    assert images == [a, b]
    assert messages[0]["content"] == [{"type": "image"}, {"type": "text", "text": "and"}]
    assert messages[1] == history[1]
    assert history[0]["content"][0]["type"] == "image_url"  # the caller's conversation is left alone


def test_only_inline_images_are_read():
    with pytest.raises(ValueError, match="inline"):
        image_bytes({"type": "image_url", "image_url": {"url": "http://10.0.0.1/secret.png"}})
    with pytest.raises(ValueError, match="base64"):
        image_bytes({"type": "image_url", "image_url": {"url": "data:image/png;base64,???"}})
    with pytest.raises(ValueError, match="couldn't read the image"):
        _vision().tokens_for(b"not a picture")

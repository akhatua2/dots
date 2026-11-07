"""The small-batch 4-bit kernel gives MLX's answer, and only takes the calls it is faster for."""

import pytest

mx = pytest.importorskip("mlx.core")
nn = pytest.importorskip("mlx.nn")

from dots.backends.mlx import small_batch  # noqa: E402


@pytest.mark.parametrize("dtype", ["bfloat16", "float16"])
@pytest.mark.parametrize("tokens", [2, 3, 5])
def test_same_result_as_mlx(tokens, dtype):
    mx.random.seed(0)
    k, n = 1536, 2048
    w, scales, biases = mx.quantize(mx.random.normal((n, k)).astype(getattr(mx, dtype)), group_size=64, bits=4)
    x = mx.random.normal((tokens, k)).astype(getattr(mx, dtype))
    expected = mx.quantized_matmul(x, w, scales, biases, transpose=True, group_size=64, bits=4).astype(mx.float32)
    got = small_batch.matmul(x, w, scales, biases, group_size=64).astype(mx.float32)
    worst = mx.max(mx.abs(expected - got)).item() / mx.max(mx.abs(expected)).item()
    assert worst < 2e-2, "within the rounding of a 16-bit result"


def test_takes_only_a_few_tokens_on_large_layers():
    big = nn.QuantizedLinear(1536, 8960, bias=False, group_size=64, bits=4)
    small = nn.QuantizedLinear(256, 256, bias=False, group_size=64, bits=4)
    few = mx.zeros((1, 4, 1536), dtype=mx.float16)
    assert small_batch.eligible(big, few)
    assert not small_batch.eligible(big, mx.zeros((1, 1, 1536), dtype=mx.float16)), "one token: MLX's fast path"
    assert not small_batch.eligible(big, mx.zeros((1, 6, 1536), dtype=mx.float16)), "six or more: registers spill"
    assert not small_batch.eligible(small, mx.zeros((1, 4, 256), dtype=mx.float16)), "small layers: MLX is as fast"


def test_a_tied_output_layer_goes_through_the_kernel_too():
    from dots.backends.mlx import small_batch as sb

    sb.install()
    emb = nn.QuantizedEmbedding(4096, 1536, group_size=64, bits=4)
    x = mx.random.normal((1, 4, 1536)).astype(mx.float16)
    assert sb.eligible(emb, x)
    expected = mx.quantized_matmul(x, emb["weight"], emb["scales"], emb["biases"], transpose=True,
                                   group_size=64, bits=4).astype(mx.float32)
    got = emb.as_linear(x).astype(mx.float32)
    assert got.shape == expected.shape
    assert mx.max(mx.abs(expected - got)).item() / mx.max(mx.abs(expected)).item() < 2e-2

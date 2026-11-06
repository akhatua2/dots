"""A Metal kernel for 4-bit layers fed a handful of tokens at once: the shape of a speculative check.

MLX has a fast path for one token (it reads each weight once) and a general one for many; between them,
for 2 to 8 tokens, the cost grows almost token for token. Checking five drafted tokens cost about 4.5 times
one token on Qwen2.5 1.5B's large layers, which ate most of what speculative decoding saves.

This kernel reads each packed weight once and applies it to every token: a 32-thread group computes R
output rows, keeps the tokens' inputs for its slice of the row in registers, and sums across the group at the
end. bf16 inputs become floats by a shift (bf16 is the top half of a float32), fp16 ones by a cast. On an M1 Pro, five tokens
through a 1536 x 8960 layer took 181 us against MLX's 269 us, 1.5 to 1.7 times faster with the same results.
Past five tokens its registers spill and it gets slower than MLX, and on small layers MLX is as fast, so it
only takes 2 to 5 tokens on large layers and hands everything else to MLX unchanged.

`install()` routes eligible calls of `mlx.nn.QuantizedLinear` through it. `DOTS_SMALL_BATCH_KERNEL=0` leaves
MLX alone.
"""

from __future__ import annotations

import os

import mlx.core as mx
import mlx.nn as nn

MIN_TOKENS, MAX_TOKENS = 2, 5
# Below this many weights a layer is quick either way, and MLX's launch is leaner than ours
MIN_WEIGHTS = 4_000_000

_SOURCE = """
    uint sg = thread_position_in_grid.x / 32;
    uint lane = thread_index_in_simdgroup;
    uint n0 = sg * R;
    if (n0 >= N) return;
    constexpr int KP = K / 8;
    constexpr int G = K / GS;
    const device uint4* x4 = (const device uint4*)x;
    float acc[R][M];
    for (int r = 0; r < R; r++) for (int m = 0; m < M; m++) acc[r][m] = 0.0f;
    for (int i = lane; i < KP; i += 32) {
        // Eight 16-bit inputs per token in one load. A bf16 becomes a float by a shift; a half by a cast.
        float xv[M][8];
        float xs[M];
        for (int m = 0; m < M; m++) {
            uint4 raw = x4[m * KP + i];
            uint words[4] = {raw.x, raw.y, raw.z, raw.w};
            xs[m] = 0.0f;
            for (int h = 0; h < 4; h++) {
                if (BF16) {
                    xv[m][2 * h] = as_type<float>(words[h] << 16);
                    xv[m][2 * h + 1] = as_type<float>(words[h] & 0xFFFF0000u);
                } else {
                    half2 pair = as_type<half2>(words[h]);
                    xv[m][2 * h] = float(pair.x);
                    xv[m][2 * h + 1] = float(pair.y);
                }
                xs[m] += xv[m][2 * h] + xv[m][2 * h + 1];
            }
        }
        int g = (i * 8) / GS;
        for (int r = 0; r < R; r++) {
            uint n = n0 + r;
            if (n >= N) break;
            // One packed word is eight 4-bit weights, lowest bits first; w = scale * q + bias
            uint packed = w[n * KP + i];
            float s = float(scales[n * G + g]);
            float b = float(biases[n * G + g]);
            float q[8];
            for (int j = 0; j < 8; j++) q[j] = float((packed >> (4 * j)) & 0xFu);
            for (int m = 0; m < M; m++) {
                float dot = 0.0f;
                for (int j = 0; j < 8; j++) dot = fma(q[j], xv[m][j], dot);
                acc[r][m] = fma(s, dot, fma(b, xs[m], acc[r][m]));
            }
        }
    }
    for (int r = 0; r < R; r++) {
        uint n = n0 + r;
        if (n >= N) break;
        for (int m = 0; m < M; m++) {
            float t = simd_sum(acc[r][m]);
            if (lane == 0) y[m * N + n] = T(t);
        }
    }
"""

_kernel = None


def matmul(x: mx.array, w: mx.array, scales: mx.array, biases: mx.array, group_size: int) -> mx.array:
    """`x @ dequantize(w).T` for x of shape (tokens, K) in bf16 or fp16, w 4-bit packed as MLX stores it."""
    global _kernel
    if _kernel is None:
        _kernel = mx.fast.metal_kernel(name="dots_small_batch_qmm", input_names=["x", "w", "scales", "biases"],
                                       output_names=["y"], source=_SOURCE)
    tokens, k = x.shape
    n = w.shape[0]
    rows = 4 if tokens <= 2 else 2  # fewer rows a group as more tokens need registers
    return _kernel(inputs=[x, w, scales, biases],
                   template=[("M", tokens), ("K", k), ("N", n), ("GS", group_size), ("R", rows),
                             ("BF16", x.dtype == mx.bfloat16), ("T", x.dtype)],
                   grid=(32 * ((n + rows - 1) // rows), 1, 1), threadgroup=(256, 1, 1),
                   output_shapes=[(tokens, n)], output_dtypes=[x.dtype])[0]


def eligible(layer: "nn.QuantizedLinear | nn.QuantizedEmbedding", x: mx.array) -> bool:
    tokens = x.size // x.shape[-1]
    return (MIN_TOKENS <= tokens <= MAX_TOKENS and x.dtype in (mx.bfloat16, mx.float16) and layer.bits == 4
            and getattr(layer, "mode", "affine") == "affine" and "biases" in layer
            and x.shape[-1] % 8 == 0 and x.shape[-1] % layer.group_size == 0
            and layer["weight"].shape[0] * x.shape[-1] >= MIN_WEIGHTS)


_original_call = None
_original_as_linear = None


def install() -> bool:
    """Route 2 to 5 token calls of large 4-bit layers through the kernel. Safe to call more than once.

    That covers the decoder's projections and, in models that tie their embeddings to the output (Qwen2.5
    among them), the output layer: a vocabulary-sized multiply on the head Mac that MLX runs through
    `QuantizedEmbedding.as_linear`.
    """
    global _original_call, _original_as_linear
    if _original_call is not None or os.environ.get("DOTS_SMALL_BATCH_KERNEL", "1") == "0":
        return _original_call is not None
    _original_call = nn.QuantizedLinear.__call__
    _original_as_linear = nn.QuantizedEmbedding.as_linear

    def call(self, x):
        if not eligible(self, x):
            return _original_call(self, x)
        flat = x.reshape(-1, x.shape[-1])
        y = matmul(flat, self["weight"], self["scales"], self["biases"], self.group_size)
        y = y.reshape(*x.shape[:-1], y.shape[-1])
        if "bias" in self:
            y = y + self["bias"]
        return y

    def as_linear(self, x):
        if not eligible(self, x):
            return _original_as_linear(self, x)
        flat = x.reshape(-1, x.shape[-1])
        y = matmul(flat, self["weight"], self["scales"], self["biases"], self.group_size)
        return y.reshape(*x.shape[:-1], y.shape[-1])

    nn.QuantizedLinear.__call__ = call
    nn.QuantizedEmbedding.as_linear = as_linear
    return True

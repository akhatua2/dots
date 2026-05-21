# MLX Backend

MLX-specific implementation.

This package owns `mlx`/`mlx_lm` imports, partial loading, load progress,
MLX model geometry inspection, MLX hidden-state wire encoding, and MLX
sampling. The rest of Dots should not import MLX directly.

Current support is direct `mlx-lm` loading. Future conversion from a
standard Hugging Face checkpoint to an MLX-ready artifact belongs in
`prepare_model()` and must declare disk/RAM/CPU/I/O requirements before
running.

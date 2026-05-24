# Backends

Backend runtimes adapt Dots work to a concrete inference engine.

Backend responsibilities:

- resolve and prepare user model ids into runnable artifacts;
- declare preparation resource requirements;
- load full or partial model assignments;
- own model cache state;
- run prefill, decode, segment forward, projection, and sampling;
- encode/decode backend-specific hidden-state payloads.

Everything that imports a framework such as MLX, vLLM, llama.cpp, or
PyTorch belongs in a backend package. Distributed workers and compute
planes should depend only on `BackendRuntime`.

Current implemented execution backend: `mlx`.

Planned CPU backend: `llama_cpp` for GGUF/llama.cpp-compatible artifacts.

# Placement

Backend-neutral placement planning.

This package owns model metadata, work-unit geometry, scheduler types,
and sharding strategy selection. It can reason about RAM, disk, CPU, I/O,
latency, and backend capabilities. It must not import inference frameworks
or assume a model format is runnable by every backend.

Model metadata is extensible through `ModelComponent`, so future models
such as Mamba, hybrid attention/state-space, and MoE architectures can
describe their real components instead of pretending every model is a
uniform transformer layer stack.

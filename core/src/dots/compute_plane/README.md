# Compute Plane

Compute-plane code plans and routes work. It should stay backend and
model-format agnostic.

Allowed dependencies:

- backend interfaces, not backend implementations;
- placement outputs;
- generation strategy interfaces.

Do not load models, convert checkpoints, inspect tokenizer/model objects,
or serialize framework tensors here.

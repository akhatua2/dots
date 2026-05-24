# App

User-facing surfaces live here.

- `api.py`: OpenAI-compatible HTTP API over a `Hub`.
- `chat/`: terminal chat UI.

This package may call public worker/hub APIs. It should not know backend
model formats, tensor serialization, or placement internals.

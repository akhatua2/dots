"""Standard one-token-at-a-time autoregressive generation."""

from __future__ import annotations

import time

from .base import GenerationRequest, GenerationResult, GenerationStrategy


class AutoregressiveGeneration(GenerationStrategy):
    name = "autoregressive"

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        runtime = request.runtime
        prefill = await runtime.prefill(
            request.conv_id,
            request.history,
            forward_hidden=request.forward_hidden,
        )
        if prefill.logits_row is None:
            return GenerationResult(
                text="",
                stats={
                    "prefill_tokens": 0,
                    "decode_tokens": 0,
                    "prefill_ms": 0.0,
                    "elapsed_ms": 0.0,
                },
            )

        token = runtime.sample_token(prefill.logits_row, request.temperature)
        stop_ids = runtime.stop_ids()
        response_tokens: list[int] = []
        decode_start = time.perf_counter()

        while len(response_tokens) < request.max_tokens and token not in stop_ids:
            text = runtime.decode_token(token)
            if request.on_token is not None:
                await request.on_token(text)
            response_tokens.append(token)
            logits_row = await runtime.decode_one(
                request.conv_id,
                token,
                forward_hidden=request.forward_hidden,
            )
            token = runtime.sample_token(logits_row, request.temperature)

        elapsed_ms = (time.perf_counter() - decode_start) * 1000
        return GenerationResult(
            text=runtime.decode_tokens(response_tokens),
            stats={
                "prefill_tokens": prefill.prefill_tokens,
                "decode_tokens": len(response_tokens),
                "prefill_ms": prefill.prefill_ms,
                "elapsed_ms": elapsed_ms,
            },
        )

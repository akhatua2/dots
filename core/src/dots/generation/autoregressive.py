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
            template_kwargs=request.template_kwargs,
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

        response_tokens: list[int] = []

        def recent() -> list[int]:
            """Window of just-generated tokens the sampler penalizes to
            avoid repetition loops."""
            ctx = request.repetition_context
            return response_tokens[-ctx:] if ctx > 0 else response_tokens

        token = runtime.sample_token(
            prefill.logits_row,
            request.temperature,
            recent_tokens=recent(),
            repetition_penalty=request.repetition_penalty,
        )
        stop_ids = runtime.stop_ids()
        # Streaming detokenizer: emits only complete UTF-8 segments, so a
        # multi-byte char (emoji/CJK) split across tokens isn't streamed
        # as broken bytes (``�``).
        detok = runtime.new_detokenizer()
        decode_start = time.perf_counter()

        while len(response_tokens) < request.max_tokens and token not in stop_ids:
            text = detok.add_token(token)
            if request.on_token is not None and text:
                await request.on_token(text)
            response_tokens.append(token)
            logits_row = await runtime.decode_one(
                request.conv_id,
                token,
                forward_hidden=request.forward_hidden,
            )
            token = runtime.sample_token(
                logits_row,
                request.temperature,
                recent_tokens=recent(),
                repetition_penalty=request.repetition_penalty,
            )

        tail = detok.finalize()
        if request.on_token is not None and tail:
            await request.on_token(tail)
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

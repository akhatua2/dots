"""Decode speed across a simulated two-Mac pool: one token at a time against speculative decoding.

The big model's layers are split in two halves on this Mac, with a sleep of one network round trip
between them, so each pass of the big model pays what a two-Mac pool pays over the internet. The draft
model runs locally, as it would on the head Mac. Output is checked token for token against one-token
decoding, so a speedup is never bought with a different answer.

    uv run python scripts/bench_decode.py
    uv run python scripts/bench_decode.py --target mlx-community/Qwen2.5-3B-Instruct-4bit --rtt 0 25 50 100
"""

from __future__ import annotations

import argparse
import asyncio
import time

PROMPTS = [
    "Explain in four sentences why the sky is blue.",
    "Write a short Python function that returns the n-th Fibonacci number, with a docstring.",
    "List five practical uses for a spare laptop, one line each.",
]

# Answers that copy what they were given: where looking the next words up in the conversation pays
EDIT_PROMPTS = [
    "Add type hints to this function and change nothing else:\n\n"
    "def merge(left, right):\n    out = []\n    i = j = 0\n    while i < len(left) and j < len(right):\n"
    "        if left[i] <= right[j]:\n            out.append(left[i])\n            i += 1\n        else:\n"
    "            out.append(right[j])\n            j += 1\n    out.extend(left[i:])\n    out.extend(right[j:])\n"
    "    return out",
    "Rewrite this paragraph in British spelling and change nothing else: The color of the organization's "
    "logo was analyzed by the program, which then optimized the center of the design to minimize gray areas.",
]


def build(repo: str):
    from mlx_lm import load

    from dots.backends.mlx.runtime import FullModelHost, MlxBackendRuntime

    model, tokenizer = load(repo)
    host = FullModelHost(model, tokenizer)
    return host, MlxBackendRuntime.from_host(host)


def split_pool(host, conv_id: str, rtt_ms: float):
    """The big model as two Macs: the first half here, the second half one round trip away."""
    import mlx.core as mx

    half = host.n_layers // 2

    async def forward(hidden, keep_last=None):
        hidden = host.forward_segment(conv_id, hidden, 0, half)
        mx.eval(hidden)  # the hidden state is materialized to go on the wire
        if rtt_ms:
            await asyncio.sleep(rtt_ms / 1000)
        return host.forward_segment(conv_id, hidden, half, host.n_layers)

    return forward


async def run(strategy, runtime, host, prompt: str, rtt_ms: float, max_tokens: int, conv_id: str):
    from dots.generation.base import GenerationRequest

    runtime.reset_conversation(conv_id)
    request = GenerationRequest(runtime=runtime, conv_id=conv_id, history=[{"role": "user", "content": prompt}],
                                max_tokens=max_tokens, temperature=0.0, repetition_penalty=1.0,
                                forward_hidden=split_pool(host, conv_id, rtt_ms))
    t0 = time.perf_counter()
    result = await strategy.generate(request)
    seconds = time.perf_counter() - t0 - result.stats["prefill_ms"] / 1000
    return result, seconds


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--target", default="mlx-community/Qwen2.5-1.5B-Instruct-4bit")
    p.add_argument("--draft", default="mlx-community/Qwen2.5-0.5B-Instruct-4bit")
    p.add_argument("--rtt", type=float, nargs="+", default=[0, 20, 50, 100], help="round trips to simulate, in ms")
    p.add_argument("--k", type=int, nargs="+", default=[2, 4, 6], help="draft lengths to try")
    p.add_argument("--max-tokens", type=int, default=96)
    p.add_argument("--adaptive", action="store_true", help="also try adaptive draft lengths, up to each k")
    p.add_argument("--lookup", action="store_true", help="also try looking guesses up in the conversation")
    p.add_argument("--prompts", choices=["general", "edit"], default="general")
    args = p.parse_args()

    from dots.generation.autoregressive import AutoregressiveGeneration
    from dots.generation.speculative import SpeculativeGeneration

    host, target = build(args.target)
    _, draft = build(args.draft)
    print(f"target {args.target} ({host.n_layers} layers, split in two)   draft {args.draft}")
    print(f"{'rtt ms':>6}  {'strategy':<14} {'tok/s':>7} {'speedup':>8} {'accepted':>9}  same output")

    prompts = EDIT_PROMPTS if args.prompts == "edit" else PROMPTS
    # (k, adaptive, lookup, with the draft model)
    variants = [(k, a, False, True) for k in args.k for a in ([False, True] if args.adaptive else [False])]
    if args.lookup:
        variants += [(k, True, True, True) for k in args.k] + [(k, True, True, False) for k in args.k]
    for rtt in args.rtt:
        base_tokens = base_seconds = 0.0
        baseline_text = []
        for i, prompt in enumerate(prompts):
            result, seconds = await run(AutoregressiveGeneration(), target, host, prompt, rtt, args.max_tokens, f"b{i}")
            baseline_text.append(result.text)
            base_tokens += result.stats["decode_tokens"]
            base_seconds += seconds
        base_rate = base_tokens / base_seconds
        print(f"{rtt:>6.0f}  {'one at a time':<14} {base_rate:>7.1f} {'1.00x':>8} {'':>9}")
        for k, adaptive, lookup, with_draft in variants:
            tokens = seconds_total = proposed = accepted = 0.0
            same = True
            for i, prompt in enumerate(prompts):
                strategy = SpeculativeGeneration(draft if with_draft else None, draft_tokens=k, adaptive=adaptive,
                                                 lookup=lookup)
                result, seconds = await run(strategy, target, host, prompt, rtt, args.max_tokens, f"s{i}")
                same &= result.text == baseline_text[i]
                tokens += result.stats["decode_tokens"]
                seconds_total += seconds
                proposed += result.stats["draft_proposed"]
                accepted += result.stats["draft_accepted"]
            rate = tokens / seconds_total
            label = ((f"lookup k<={k}" if with_draft else f"lookup only k<={k}") if lookup
                     else f"draft k<={k}" if adaptive else f"draft k={k}")
            print(f"{rtt:>6.0f}  {label:<18} {rate:>7.1f} {rate / base_rate:>7.2f}x "
                  f"{accepted / proposed:>8.0%}  {'yes' if same else 'NO'}")


if __name__ == "__main__":
    asyncio.run(main())

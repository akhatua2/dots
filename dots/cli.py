"""CLI entry point."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def _parse_workers(s: str) -> list[tuple[str, int]]:
    if not s:
        return []
    out = []
    for token in s.split(","):
        host, _, port = token.strip().partition(":")
        if not port:
            raise argparse.ArgumentTypeError(f"expected host:port, got {token!r}")
        out.append((host, int(port)))
    return out


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    p = argparse.ArgumentParser(prog="dots")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="run as a worker (waits for coordinator)")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=5555)

    p_chat = sub.add_parser("chat", help="run as coordinator and start chat loop")
    p_chat.add_argument(
        "--model",
        default="mlx-community/Qwen2.5-0.5B-Instruct-4bit",
        help="mlx-lm model id or local path",
    )
    p_chat.add_argument(
        "--workers",
        default="",
        type=_parse_workers,
        help="comma-separated remote workers, e.g. 10.30.1.2:5555,10.30.1.3:5555",
    )
    p_chat.add_argument(
        "--prompt",
        default=None,
        help="if set, run this prompt once and exit (non-interactive)",
    )
    p_chat.add_argument(
        "--max-tokens",
        type=int,
        default=128,
        help="max tokens to generate per response",
    )
    p_chat.add_argument(
        "--temperature",
        type=float,
        default=0.7,
        help="sampling temperature (0 = greedy)",
    )

    args = p.parse_args()

    if args.cmd == "serve":
        from .worker import serve
        try:
            asyncio.run(serve(args.host, args.port))
        except KeyboardInterrupt:
            sys.exit(0)
    elif args.cmd == "chat":
        from .coordinator import chat
        try:
            asyncio.run(chat(args.model, args.workers,
                             one_shot=args.prompt, max_tokens=args.max_tokens,
                             temperature=args.temperature))
        except KeyboardInterrupt:
            sys.exit(0)


if __name__ == "__main__":
    main()

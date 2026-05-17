"""CLI entry point: `dots hub` and `dots worker`."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def _parse_hub_addr(s: str) -> tuple[str, int]:
    host, _, port = s.rpartition(":")
    if not host or not port:
        raise argparse.ArgumentTypeError(f"expected host:port, got {s!r}")
    return host, int(port)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    p = argparse.ArgumentParser(prog="dots")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_hub = sub.add_parser("hub", help="run the dots hub (chat REPL + worker pool)")
    p_hub.add_argument("--port", type=int, default=5555)
    p_hub.add_argument("--host", default="0.0.0.0")
    p_hub.add_argument(
        "--model",
        default="mlx-community/Qwen2.5-0.5B-Instruct-4bit",
        help="mlx-lm model id (auto-downloaded on first run)",
    )

    p_worker = sub.add_parser("worker", help="connect to a hub and serve a shard")
    p_worker.add_argument(
        "hub",
        type=_parse_hub_addr,
        help="hub address as host:port (e.g. 100.106.22.71:5555)",
    )
    p_worker.add_argument(
        "--prompt",
        default=None,
        help="non-interactive: send one prompt and exit",
    )
    p_worker.add_argument(
        "--max-tokens", type=int, default=128,
        help="max tokens for --prompt mode",
    )

    args = p.parse_args()

    if args.cmd == "hub":
        from .hub import Hub
        hub = Hub(port=args.port, model_id=args.model, host=args.host)
        try:
            asyncio.run(hub.start())
        except KeyboardInterrupt:
            sys.exit(0)
    elif args.cmd == "worker":
        from .worker import Worker
        host, port = args.hub
        worker = Worker(host, port, one_shot=args.prompt, max_tokens=args.max_tokens)
        try:
            asyncio.run(worker.start())
        except KeyboardInterrupt:
            sys.exit(0)


if __name__ == "__main__":
    main()

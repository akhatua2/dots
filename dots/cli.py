"""CLI entry point: `dots hub` and `dots worker`.

By default we run in quiet mode — only the banner, the streaming response,
the per-turn stats line, and errors hit your terminal. Worker join/leave
events, model load progress, and HTTP request logs are suppressed. Pass
`--verbose` (or `-v`) to see all of it.
"""
from __future__ import annotations

# These environment variables have to be set before any module that reads
# them (huggingface_hub, transformers) is imported. We default to quiet
# and the verbose path below restores progress bars if requested.
import os
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import argparse
import asyncio
import logging
import sys


def _parse_hub_addr(s: str) -> tuple[str, int]:
    host, _, port = s.rpartition(":")
    if not host or not port:
        raise argparse.ArgumentTypeError(f"expected host:port, got {s!r}")
    return host, int(port)


def _configure_logging(verbose: bool) -> None:
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # Always quiet these no matter the level — they spam INFO during model
    # downloads and the user almost never wants per-request HTTP traces.
    if not verbose:
        for noisy in ("httpx", "urllib3", "huggingface_hub",
                      "transformers", "filelock"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    else:
        os.environ.pop("HF_HUB_DISABLE_PROGRESS_BARS", None)


def main() -> None:
    p = argparse.ArgumentParser(prog="dots")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="show internal logs (worker join/leave, model loading, "
                        "HTTP requests, HF progress bars)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_hub = sub.add_parser("hub", help="run the dots hub (orchestrator + chat pool)")
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
        "--prompt", default=None,
        help="non-interactive: send one prompt and exit",
    )
    p_worker.add_argument(
        "--max-tokens", type=int, default=128,
        help="max tokens for --prompt mode",
    )

    args = p.parse_args()
    _configure_logging(args.verbose)
    from . import ui
    ui.set_verbose(args.verbose)

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
        worker = Worker(host, port,
                        one_shot=args.prompt, max_tokens=args.max_tokens,
                        headless=args.headless)
        try:
            asyncio.run(worker.start())
        except KeyboardInterrupt:
            sys.exit(0)


if __name__ == "__main__":
    main()

"""CLI entry point: `dots hub`, `dots chat`, `dots worker`.

By default we run in quiet mode — only the banner, the streaming
response, the per-turn stats line, and errors hit your terminal.
Pass `--verbose` (or `-v`) to surface internal logs and progress bars.
"""
from __future__ import annotations

# These environment variables must be set before any module that reads
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

from .util import load_last_hub


def _parse_hub_addr(s: str) -> tuple[str, int]:
    host, _, port = s.rpartition(":")
    if not host or not port:
        raise argparse.ArgumentTypeError(f"expected host:port, got {s!r}")
    return host, int(port)


def _resolve_hub_addr(arg: str | None) -> tuple[str, int]:
    """Parse a host:port string, falling back to the last-used hub written
    to ~/.dots/last_hub by a previous successful connect."""
    if arg is not None:
        return _parse_hub_addr(arg)
    saved = load_last_hub()
    if saved is None:
        sys.stderr.write(
            "no hub address given and no remembered hub in ~/.dots/last_hub\n"
            "usage: dots chat HOST:PORT   (or)   dots worker HOST:PORT\n"
        )
        sys.exit(2)
    return _parse_hub_addr(saved)


def _configure_logging(verbose: bool) -> None:
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
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
                   help="show internal logs (model loading, HTTP, "
                        "progress bars, etc.)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_hub = sub.add_parser("hub", help="run the dots hub (orchestrator + chat pool)")
    p_hub.add_argument("--port", type=int, default=5555)
    p_hub.add_argument("--host", default="0.0.0.0")
    p_hub.add_argument(
        "--model",
        default="mlx-community/Qwen2.5-0.5B-Instruct-4bit",
        help="mlx-lm model id (auto-downloaded on first run)",
    )

    p_chat = sub.add_parser(
        "chat",
        help="open the interactive chat. donates compute as a side effect.",
    )
    p_chat.add_argument(
        "hub", nargs="?", default=None,
        help="hub address as host:port; defaults to ~/.dots/last_hub",
    )
    p_chat.add_argument(
        "--prompt", default=None,
        help="non-interactive: send one prompt and exit",
    )
    p_chat.add_argument(
        "--max-tokens", type=int, default=128,
        help="max response tokens for --prompt mode",
    )

    p_worker = sub.add_parser(
        "worker",
        help="donate compute to a hub. no chat REPL.",
    )
    p_worker.add_argument(
        "hub", nargs="?", default=None,
        help="hub address as host:port; defaults to ~/.dots/last_hub",
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
        return

    # Both chat and worker need a hub address (with fallback to ~/.dots/last_hub).
    host, port = _resolve_hub_addr(args.hub)
    from .worker import Worker

    if args.cmd == "chat":
        worker = Worker(
            host, port,
            one_shot=args.prompt, max_tokens=args.max_tokens,
            headless=False,
        )
        try:
            asyncio.run(worker.start())
        except KeyboardInterrupt:
            sys.exit(0)
    elif args.cmd == "worker":
        worker = Worker(host, port, headless=True)
        try:
            asyncio.run(worker.start())
        except KeyboardInterrupt:
            sys.exit(0)


if __name__ == "__main__":
    main()

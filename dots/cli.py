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
import getpass
import logging
import signal
import subprocess
import sys
import time
import uuid
from typing import Optional

from .util import (
    clear_worker_pid,
    derive_account_id,
    dots_home,
    load_account_alias,
    load_account_email,
    load_account_name,
    load_last_hub,
    load_or_create_account,
    read_live_worker_pid,
    save_account_alias,
    save_account_email,
    save_account_id,
    save_account_name,
    write_worker_pid,
)


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


# ─────────────────────────── auto-spawned worker ────────────────────────────


def _spawn_companion_worker(hub_addr: str) -> Optional[subprocess.Popen]:
    """Start a `dots worker` subprocess in the background so the user
    contributes compute while their `dots chat` session is open. Returns
    the Popen handle so the caller can terminate it on exit. Returns
    None if a worker is already running on this machine (so we don't
    duplicate the contribution)."""
    if read_live_worker_pid() is not None:
        return None
    proc = subprocess.Popen(
        [sys.executable, "-m", "dots.cli", "worker", hub_addr],
        # Detach so a parent Ctrl-C doesn't immediately yank it; we'll
        # terminate it explicitly when the chat exits.
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


def _shutdown_companion_worker(proc: subprocess.Popen) -> None:
    """Terminate a companion worker subprocess cleanly. Falls back to
    SIGKILL if it doesn't exit promptly."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


# ─────────────────────────── account subcommand helpers ────────────────────


def _do_account_show() -> None:
    """`dots account show` — print account_id, alias, name, email."""
    account_id = load_or_create_account()
    alias = load_account_alias()
    name = load_account_name()
    email = load_account_email()
    sys.stdout.write(f"account_id: {account_id}\n")
    sys.stdout.write(f"alias:      {alias if alias else '(unset)'}\n")
    sys.stdout.write(f"name:       {name if name else '(unset)'}\n")
    sys.stdout.write(f"email:      {email if email else '(unset)'}\n")


def _prompt(label: str, default: str | None = None) -> str:
    """Prompt the user, allowing empty input to keep the default."""
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val if val else (default or "")


def _do_account_create() -> None:
    """Interactive account setup.

    Asks for name, email, alias, and (optionally) a password. With a
    password, the account_id is *deterministically derived* from
    name+email+password — so re-running this command with the same
    inputs reproduces the same account_id. That's the recovery story:
    lose your `~/.dots/account` file, re-enter the inputs, get your
    credit history back.

    Without a password, we generate a random UUID. Simpler, but if you
    lose the file the account is unrecoverable.
    """
    home = dots_home()
    account_file = home / "account"
    existing = None
    if account_file.exists():
        existing = account_file.read_text().strip()
        sys.stdout.write(f"An account already exists: {existing}\n")
        ans = input("Overwrite? (y/N): ").strip().lower()
        if ans != "y":
            sys.stdout.write("Cancelled.\n")
            return

    sys.stdout.write("\nSetting up your dots account.\n\n")
    name = _prompt("Your name", default=load_account_name())
    email = _prompt("Your email", default=load_account_email())
    alias = _prompt("Short alias for logs (optional)",
                    default=load_account_alias())

    sys.stdout.write(
        "\nOptional: set a password to make this account recoverable.\n"
        "With a password, the account ID is derived from "
        "(name + email + password). If you lose your account file or\n"
        "set up dots on a new device, re-entering the same inputs gets\n"
        "you back the same account ID — and your credit history.\n"
        "Without one, we generate a random UUID (not recoverable).\n\n"
    )
    pw1 = getpass.getpass("Password (or just hit enter to skip): ")
    if pw1:
        if not name or not email:
            sys.stdout.write(
                "Password-derived accounts need name and email "
                "to be set. Cancelled.\n"
            )
            return
        pw2 = getpass.getpass("Confirm password: ")
        if pw1 != pw2:
            sys.stdout.write("Passwords don't match. Cancelled.\n")
            return
        account_id = derive_account_id(name, email, pw1)
    else:
        account_id = "acc-" + uuid.uuid4().hex

    save_account_id(account_id)
    if name:
        save_account_name(name)
    if email:
        save_account_email(email)
    if alias:
        save_account_alias(alias)

    sys.stdout.write(f"\naccount_id: {account_id}\n")
    sys.stdout.write(f"saved to:   {account_file}\n")


# ────────────────────────────────── main ────────────────────────────────────


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
    p_hub.add_argument(
        "--http-port", type=int, default=None, metavar="PORT",
        help="also expose an OpenAI-compatible HTTP API on this port "
             "(POST /v1/chat/completions, GET /v1/models)",
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
    p_chat.add_argument(
        "--no-worker", action="store_true",
        help="don't auto-spawn a companion `dots worker` subprocess. "
             "by default, if no worker is running locally, chat starts "
             "one so you contribute compute while you chat.",
    )

    p_worker = sub.add_parser(
        "worker",
        help="donate compute to a hub. no chat REPL.",
    )
    p_worker.add_argument(
        "hub", nargs="?", default=None,
        help="hub address as host:port; defaults to ~/.dots/last_hub",
    )
    p_worker.add_argument(
        "--max-ram", type=float, default=None, metavar="GB",
        help="maximum gigabytes of model weights this worker will hold. "
             "the hub treats it as a layer-count cap when planning. "
             "defaults to no cap.",
    )

    p_account = sub.add_parser(
        "account",
        help="manage this device's dots account (who earns/spends CU).",
    )
    p_account_sub = p_account.add_subparsers(dest="account_cmd", required=True)
    p_account_sub.add_parser(
        "create",
        help="interactive setup: name, email, optional password for recovery",
    )
    p_account_sub.add_parser(
        "show", help="print this device's account_id, alias, name, email",
    )
    p_account_alias = p_account_sub.add_parser(
        "set-alias", help="cosmetic short handle for log lines",
    )
    p_account_alias.add_argument("alias")
    p_account_name = p_account_sub.add_parser(
        "set-name", help="full human-readable name (local only, not sent on HELLO)",
    )
    p_account_name.add_argument("name")
    p_account_email = p_account_sub.add_parser(
        "set-email", help="contact email (local only, not sent on HELLO)",
    )
    p_account_email.add_argument("email")

    args = p.parse_args()
    _configure_logging(args.verbose)
    from . import ui
    ui.set_verbose(args.verbose)

    if args.cmd == "hub":
        from .hub import Hub
        hub = Hub(
            port=args.port, model_id=args.model, host=args.host,
            http_port=args.http_port,
        )
        try:
            asyncio.run(hub.start())
        except KeyboardInterrupt:
            sys.exit(0)
        return

    if args.cmd == "account":
        if args.account_cmd == "create":
            _do_account_create()
            return
        if args.account_cmd == "show":
            _do_account_show()
            return
        if args.account_cmd == "set-alias":
            save_account_alias(args.alias)
            sys.stdout.write(f"alias set to: {args.alias}\n")
            return
        if args.account_cmd == "set-name":
            save_account_name(args.name)
            sys.stdout.write(f"name set to: {args.name}\n")
            return
        if args.account_cmd == "set-email":
            save_account_email(args.email)
            sys.stdout.write(f"email set to: {args.email}\n")
            return

    host, port = _resolve_hub_addr(args.hub)
    from .worker import Worker

    if args.cmd == "chat":
        # Decide whether to auto-spawn a companion worker subprocess so
        # this session contributes compute while the user chats.
        companion: Optional[subprocess.Popen] = None
        if not args.no_worker:
            companion = _spawn_companion_worker(f"{host}:{port}")
            if companion is not None:
                # Give the companion a brief head start so the hub has at
                # least one worker by the time the chat client connects.
                # Without this, the first prompt would hit an empty pool.
                time.sleep(2.0)

        # Chat clients get a fresh per-session UUID. The persistent UUID
        # from ~/.dots/identity belongs to the (possibly companion-spawned)
        # `dots worker` process; if we reused it here the hub would think
        # they're the same registration and kick the worker out.
        worker = Worker(
            host, port,
            one_shot=args.prompt, max_tokens=args.max_tokens,
            headless=False, compute=False,
            identity=str(uuid.uuid4()),
        )
        try:
            asyncio.run(worker.start())
        except KeyboardInterrupt:
            pass
        finally:
            if companion is not None:
                _shutdown_companion_worker(companion)
    elif args.cmd == "worker":
        worker = Worker(host, port, headless=True, max_load_gb=args.max_ram)
        # Record our PID so a sibling `dots chat` knows there's already a
        # local worker and doesn't auto-spawn a duplicate.
        write_worker_pid(os.getpid())
        # Python's default SIGTERM handler just exits the process without
        # running finally blocks, so the companion-worker case (where chat
        # terminates us with SIGTERM on exit) would leave the PID file
        # behind. Re-route SIGTERM to a KeyboardInterrupt so the cleanup
        # in the `finally` clause below actually runs.
        signal.signal(signal.SIGTERM,
                      lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        try:
            asyncio.run(worker.start())
        except KeyboardInterrupt:
            pass
        finally:
            clear_worker_pid(only_if=os.getpid())


if __name__ == "__main__":
    main()

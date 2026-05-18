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

# Load a project-local .env file BEFORE any module that reads from the
# environment is imported. This is how users typically configure
# HF_TOKEN, HF_HUB_ENABLE_HF_TRANSFER, DOTS_TFLOPS, etc.
from .util import load_dotenv_into_env as _load_dotenv
_load_dotenv()

# `hf_xet` is a hard dependency (declared in pyproject.toml). It's the
# successor to `hf_transfer` — same idea (Rust-backed parallel download)
# but using HuggingFace's content-addressable Xet storage. Opt every
# dots run in; users can disable via HF_XET_HIGH_PERFORMANCE=0.
os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")

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


def _spawn_companion_worker(
    hub_addr: str,
    *,
    max_ram_gb: float | None = None,
) -> Optional[subprocess.Popen]:
    """Start a `dots worker` subprocess in the background so the user
    contributes compute while their `dots chat` session is open. Returns
    the Popen handle so the caller can terminate it on exit. Returns
    None if a worker is already running on this machine (so we don't
    duplicate the contribution).

    Flags relevant to worker contribution (currently `--max-ram`) are
    plumbed through so `dots chat ... --max-ram 5` runs a capped
    companion in the same way `dots worker ... --max-ram 5` would.
    """
    if read_live_worker_pid() is not None:
        return None
    cmd = [sys.executable, "-m", "dots.cli", "worker", hub_addr, "--verbose"]
    if max_ram_gb is not None:
        cmd += ["--max-ram", str(max_ram_gb)]
    # Route output to a rolling log file so crashes are diagnosable.
    # `dots chat` previously sent these to /dev/null, which made silent
    # worker exits look like a hung pre-flight in the chat UI.
    log_path = dots_home() / "worker.log"
    log_fh = open(log_path, "ab", buffering=0)
    proc = subprocess.Popen(
        cmd,
        # Detach so a parent Ctrl-C doesn't immediately yank it; we'll
        # terminate it explicitly when the chat exits.
        start_new_session=True,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    return proc


def _shutdown_companion_worker(proc: subprocess.Popen) -> None:
    """Terminate a companion worker subprocess cleanly. Falls back to
    SIGKILL if it doesn't exit promptly OR if the user is mashing
    Ctrl-C and doesn't want to wait. The companion is detached, so
    even a hard kill leaves no orphans for the parent shell."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    except KeyboardInterrupt:
        # User pressed Ctrl-C while we were waiting — they want out
        # immediately. Hard-kill and stop waiting.
        try:
            proc.kill()
        except Exception:
            pass
        return
    # First attempt timed out; escalate to SIGKILL.
    try:
        proc.kill()
        proc.wait(timeout=2)
    except (subprocess.TimeoutExpired, KeyboardInterrupt, Exception):
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


# ─────────────────────────── credits subcommand ─────────────────────────────


def _do_credits(hub_arg: str | None, *, show_all: bool) -> None:
    """`dots credits` — query the hub's /v1/credits endpoint and print
    either your account balance or, with --all, every account."""
    import json
    import urllib.error
    import urllib.request

    # The credits subcommand talks HTTP (not the dots TCP protocol).
    # Default port for the OpenAI-compatible API is 8080. If the user
    # passed only a host:port, we trust them. Otherwise resolve via
    # last_hub but swap the port for 8080.
    if hub_arg:
        host, _, port_s = hub_arg.rpartition(":")
        if not host or not port_s:
            sys.stderr.write(f"expected host:port, got {hub_arg!r}\n")
            sys.exit(2)
        host, port = host, int(port_s)
    else:
        saved = load_last_hub()
        if saved is None:
            sys.stderr.write("no hub address given and no remembered hub\n")
            sys.exit(2)
        host = saved.rsplit(":", 1)[0]
        port = 8080  # convention: HTTP API on 8080

    url = f"http://{host}:{port}/v1/credits"
    try:
        with urllib.request.urlopen(url, timeout=5.0) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        sys.stderr.write(f"could not reach {url}: {e}\n")
        sys.stderr.write("(the hub may not have --http-port enabled)\n")
        sys.exit(1)

    my_account = load_or_create_account()
    accounts = data.get("accounts", {})

    if show_all:
        if not accounts:
            sys.stdout.write("(ledger is empty)\n")
            return
        rows = sorted(accounts.items(),
                      key=lambda kv: kv[1].get("balance_cu", 0), reverse=True)
        sys.stdout.write(f"{'account':<40} {'alias':<15} "
                         f"{'earned (CU)':>12} {'spent (CU)':>12} "
                         f"{'balance (CU)':>13}\n")
        for aid, a in rows:
            marker = " ← you" if aid == my_account else ""
            alias = a.get("alias") or "—"
            sys.stdout.write(
                f"{aid:<40} {alias:<15} "
                f"{a.get('earned_cu', 0):>12.4f} "
                f"{a.get('spent_cu', 0):>12.4f} "
                f"{a.get('balance_cu', 0):>13.4f}{marker}\n"
            )
        return

    mine = accounts.get(my_account)
    if mine is None:
        sys.stdout.write(f"account {my_account}: no activity yet\n")
        return
    sys.stdout.write(f"account: {my_account}\n")
    sys.stdout.write(f"alias:   {mine.get('alias') or '(unset)'}\n")
    sys.stdout.write(f"earned:  {mine.get('earned_cu', 0):.4f} CU\n")
    sys.stdout.write(f"spent:   {mine.get('spent_cu', 0):.4f} CU\n")
    sys.stdout.write(f"balance: {mine.get('balance_cu', 0):.4f} CU\n")


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
        "--http-port", type=int, default=8080, metavar="PORT",
        help="port for the OpenAI-compatible HTTP API "
             "(POST /v1/chat/completions, GET /v1/models, GET /v1/credits). "
             "defaults to 8080. pass --http-port 0 to disable.",
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
    p_chat.add_argument(
        "--max-ram", type=float, default=None, metavar="GB",
        help="cap the RAM the auto-spawned companion worker pools. "
             "Mirrors `dots worker --max-ram`. Ignored when --no-worker.",
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

    p_credits = sub.add_parser(
        "credits",
        help="show your CU balance from a hub's /v1/credits endpoint.",
    )
    p_credits.add_argument(
        "hub", nargs="?", default=None,
        help="hub HTTP api as host:port (defaults to last hub on port 8080)",
    )
    p_credits.add_argument(
        "--all", action="store_true",
        help="show every account, not just yours",
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

    if args.cmd == "credits":
        _do_credits(args.hub, show_all=args.all)
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
            companion = _spawn_companion_worker(
                f"{host}:{port}", max_ram_gb=args.max_ram,
            )
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

"""Tiny terminal UI helpers — banner, prompt, spinner, stats."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()


def banner(model: str, pipeline: list[str]) -> None:
    """Show a startup banner describing the cluster."""
    body = Text()
    body.append("model      ", style="dim")
    body.append(f"{model}\n", style="bold")
    body.append("pipeline   ", style="dim")
    body.append("  →  ".join(pipeline), style="cyan")
    body.append("\n\n", style="")
    body.append("Ctrl-D to exit", style="dim italic")
    console.print(Panel(body, title="[bold magenta]· · · dots[/]", border_style="magenta",
                        expand=False, padding=(1, 2)))


def info(msg: str) -> None:
    console.print(f"[dim]{msg}[/dim]")


def error(msg: str) -> None:
    console.print(f"[bold red]error[/bold red] {msg}")


def stats(prefill_tokens: int, prefill_ms: float,
          decode_tokens: int, decode_s: float) -> None:
    parts = []
    if prefill_tokens:
        parts.append(f"prefill {prefill_tokens}tok/{prefill_ms:.0f}ms")
    if decode_tokens:
        tps = decode_tokens / decode_s if decode_s > 0 else 0.0
        parts.append(f"decode {decode_tokens}tok/{decode_s:.2f}s = {tps:.1f} tok/s")
    console.print(f"\n[dim]↳ {'  ·  '.join(parts)}[/dim]")

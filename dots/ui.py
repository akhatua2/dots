"""Tiny terminal UI helpers — banner, spinner, stats.

`info()` only prints when verbose mode is on (see `set_verbose`). Banner,
stats, and errors always print — they're the bits the user actually wants
in a normal session. Everything debuggy goes through info().
"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

_verbose = False


def set_verbose(v: bool) -> None:
    global _verbose
    _verbose = v


def is_verbose() -> bool:
    return _verbose


def banner(model: str, hub_addr: str, layer_range: tuple[int, int] | None = None,
           version: str = "0.0.1") -> None:
    """Compact startup banner. `···` at col 0, key=value rows indented to
    align under 'dots'."""
    if layer_range is not None:
        shard_str = f"layers {layer_range[0]}:{layer_range[1]}"
    else:
        shard_str = "not assigned"

    indent = "     "
    rows = [
        ("model", model),
        ("hub", hub_addr),
        ("shard", shard_str),
    ]

    out = Text()
    out.append("···  ", style="bold magenta")
    out.append("dots ", style="bold")
    out.append(f"v{version}\n", style="dim")
    for k, v in rows:
        out.append(indent)
        out.append(f"{k:<9}", style="dim")
        out.append(f"{v}\n")
    console.print(out)


def banner_text(model: str, hub_addr: str, layer_range: tuple[int, int] | None,
                version: str = "0.0.1") -> str:
    """Plain-text banner (no ANSI). Used as a fallback when styling is
    inconvenient. The interactive UI uses `banner_segments` instead."""
    if layer_range is not None:
        shard_str = f"layers {layer_range[0]}:{layer_range[1]}"
    else:
        shard_str = "not assigned"
    lines = [
        f"···  dots v{version}",
        f"     model     {model}",
        f"     hub       {hub_addr}",
        f"     shard     {shard_str}",
    ]
    return "\n".join(lines)


_ART_LINES = [
    "  · ✦ ·    ",
    " ✦ · · ✦   ",
    " ✦ · · ✦   ",
    "  · ✦ ·    ",
]


def _style_art_line(line: str) -> list[tuple[str, str]]:
    """Color stars and dots distinctly so the constellation has weight."""
    out: list[tuple[str, str]] = []
    for ch in line:
        if ch == "✦":
            out.append(("class:banner.star", ch))
        elif ch == "·":
            out.append(("class:banner.dot", ch))
        else:
            out.append(("", ch))
    return out


def banner_segments(model: str, hub_addr: str,
                    layer_range: tuple[int, int] | None,
                    version: str = "0.0.1") -> list[tuple[str, str]]:
    """Banner as `(style, text)` tuples for FormattedTextControl. Four-row
    constellation art on the left, vertically centered against the four
    text rows (dots/model/hub/shard) on the right."""
    if layer_range is not None:
        shard_str = f"layers {layer_range[0]}:{layer_range[1]}"
    else:
        shard_str = "not assigned"
    text_rows = [("model", model), ("hub", hub_addr), ("shard", shard_str)]

    out: list[tuple[str, str]] = []
    # Row 1: art + project name + version
    out.extend(_style_art_line(_ART_LINES[0]))
    out.append(("class:banner.name", "dots "))
    out.append(("class:banner.version", f"v{version}\n"))
    # Rows 2-4: art + key/value pairs
    for art_line, (key, val) in zip(_ART_LINES[1:], text_rows, strict=True):
        out.extend(_style_art_line(art_line))
        out.append(("class:banner.key", f"{key:<9}"))
        out.append(("class:banner.value", f"{val}\n"))
    return out


def info(msg: str) -> None:
    if _verbose:
        console.print(f"[dim]{msg}[/dim]")


def notice(msg: str) -> None:
    """Always-visible status (e.g. 'hub disconnected — exiting')."""
    console.print(f"[dim]{msg}[/dim]")


def error(msg: str) -> None:
    console.print(f"[bold red]error[/bold red] {msg}")


def stats(prefill_tokens: int, prefill_ms: float,
          decode_tokens: int, decode_s: float) -> None:
    """Kept for callers that still want a one-line per-response stats summary
    (currently only the one-shot --prompt path)."""
    parts = []
    if prefill_tokens:
        parts.append(f"prefill {prefill_tokens}tok/{prefill_ms:.0f}ms")
    if decode_tokens:
        tps = decode_tokens / decode_s if decode_s > 0 else 0.0
        parts.append(f"decode {decode_tokens}tok/{decode_s:.2f}s = {tps:.1f} tok/s")
    console.print(f"\n[dim]↳ {'  ·  '.join(parts)}[/dim]")


def divider(tps: float | None = None) -> None:
    """Draw a horizontal rule across the terminal, with the latest tok/s on
    the right if we have one. Printed between turns so the user always sees
    a stable status indicator."""
    width = max(20, console.width - 1)
    if tps is None or tps <= 0:
        rule = "─" * width
        console.print(f"[dim]{rule}[/dim]")
    else:
        tag = f"  {tps:.1f} tok/s"
        rule = "─" * (width - len(tag))
        console.print(f"[dim]{rule}[/dim][dim cyan]{tag}[/dim cyan]")

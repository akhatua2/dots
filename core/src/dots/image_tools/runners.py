"""How a Mac draws with each family of image model, from `runners.toml`: never a command from the network.

A pool that draws names its model and the original it was made from. A worker offers to draw the originals it
has a runner for on this Mac, and draws a job with that runner and the job's model, which must be a Hugging Face
repo name. A Mac with no runner for a pool's model doesn't draw there. See docs/plans/pools-from-a-link.md.
"""

from __future__ import annotations

import platform
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..images import ImageTool

_HERE = Path(__file__).parent
_CONFIG = _HERE / "runners.toml"
# A model a job names: owner/name on Hugging Face, so it can't be taken for a flag or a path
_REPO = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")


@dataclass(frozen=True)
class Runner:
    name: str
    originals: tuple[str, ...]
    base_model: str
    draw: tuple[str, ...]
    edit: tuple[str, ...] | None
    # The oldest macOS its tool runs on
    macos: str

    def runs_here(self) -> bool:
        if sys.platform != "darwin":
            return False
        return _version(platform.mac_ver()[0]) >= _version(self.macos)

    def tool(self, model: str) -> ImageTool:
        """The tool that draws with `model`, a repo made from one of this runner's originals."""
        if not _REPO.match(model):
            raise ValueError(f"not a model name: {model!r}")
        return ImageTool.with_settings(model=model, command=self.draw, edit_command=self.edit, base_model=self.base_model)


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3]) or (0,)


def load(path: Path = _CONFIG) -> list[Runner]:
    """Every runner dots knows, whether or not it runs on this Mac."""
    with open(path, "rb") as f:
        entries = tomllib.load(f).get("runner", [])

    def command(words: list[str] | None) -> tuple[str, ...] | None:
        return tuple(w.replace("{tools}", str(_HERE)) for w in words) if words else None

    return [Runner(name=e["name"], originals=tuple(e["originals"]), base_model=e["base_model"], draw=command(e["draw"]),
                   edit=command(e.get("edit")), macos=e.get("macos", "0")) for e in entries]


def here() -> list[Runner]:
    """The runners this Mac can use."""
    return [r for r in load() if r.runs_here()]


def for_original(original: str | None, runners: list[Runner]) -> Runner | None:
    return next((r for r in runners if original in r.originals), None) if original else None

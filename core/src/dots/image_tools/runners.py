"""How a Mac draws with each family of image model, from `runners.toml`: never a command from the network.

A pool that draws names its model and the original it was made from. A worker offers to draw the originals it
has a runner for on this Mac, and draws a job with that runner and the job's model, which must be a Hugging Face
repo name. A Mac with no runner for a pool's model doesn't draw there. See docs/plans/pools-from-a-link.md.
"""

from __future__ import annotations

import platform
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..images import ImageTool
from ..system.util import dots_home

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
    # "mflux": the tool reads the pool's model. "sdcpp": stable-diffusion.cpp, which reads the same original from
    # files of its own (`files`: each flag of the tool and the file on Hugging Face it takes, owner/repo/path)
    style: str = "mflux"
    files: tuple[tuple[str, str], ...] = ()
    extra: tuple[str, ...] = ()
    steps: int | None = None

    def runs_here(self) -> bool:
        """On a new enough macOS, with its tool installed"""
        if sys.platform != "darwin" or _version(platform.mac_ver()[0]) < _version(self.macos):
            return False
        program = self.draw[0]
        return Path(program).is_file() if "/" in program else shutil.which(program) is not None

    def tool(self, model: str) -> ImageTool:
        """The tool that draws with `model`, a repo made from one of this runner's originals. A runner that reads
        files of its own fetches them first (once: they stay in the Hugging Face cache), so call it off the loop."""
        if not _REPO.match(model):
            raise ValueError(f"not a model name: {model!r}")
        return ImageTool.with_settings(model=model, command=self.draw, edit_command=self.edit, base_model=self.base_model,
                                       steps=self.steps, style=self.style, files=self._local_files(), extra=self.extra)

    def _local_files(self) -> tuple[tuple[str, str], ...]:
        if not self.files:
            return ()
        from huggingface_hub import hf_hub_download

        out = []
        for flag, ref in self.files:
            owner, name, filename = ref.split("/", 2)
            out.append((flag, hf_hub_download(f"{owner}/{name}", filename)))
        return tuple(out)


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3]) or (0,)


def load(path: Path = _CONFIG) -> list[Runner]:
    """Every runner dots knows, whether or not it runs on this Mac."""
    with open(path, "rb") as f:
        entries = tomllib.load(f).get("runner", [])

    def command(words: list[str] | None) -> tuple[str, ...] | None:
        return tuple(w.replace("{tools}", str(_HERE)).replace("{home}", str(dots_home())) for w in words) if words else None

    return [Runner(name=e["name"], originals=tuple(e["originals"]), base_model=e.get("base_model", ""),
                   draw=command(e["draw"]), edit=command(e.get("edit")), macos=e.get("macos", "0"),
                   style=e.get("style", "mflux"), files=tuple(e.get("files", {}).items()),
                   extra=tuple(e.get("extra", ())), steps=e.get("steps")) for e in entries]


def here() -> list[Runner]:
    """The runners this Mac can use."""
    return [r for r in load() if r.runs_here()]


def for_original(original: str | None, runners: list[Runner]) -> Runner | None:
    """The first runner for models made from `original`: runners.toml lists the faster ones first"""
    return next((r for r in runners if original in r.originals), None) if original else None

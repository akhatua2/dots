"""What a Hugging Face link runs: read from the repo itself, for starting a pool from a link.

A text model's `config.json` names its architecture; the engine runs that architecture or it doesn't. An image
model built for mflux says so in its library; its family can be read from the original model it names, when that
model's files can be read (some originals are gated). Nothing here names a model: every answer comes from the
repo. See docs/plans/pools-from-a-link.md.
"""

from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import asdict, dataclass

_REPO = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
# What Hugging Face calls a model that writes text, when its repo says
_TEXT_TASKS = {"text-generation", "image-text-to-text", "text2text-generation", "conversational"}
_SITE = re.compile(r"^(https?://)?(www\.)?(huggingface\.co/|hf\.co/)?", re.IGNORECASE)


@dataclass
class ModelDescription:
    id: str
    name: str
    # "text", "pictures", or "unknown" when the repo doesn't say
    kind: str
    # The architecture (text) or pipeline class (pictures), when the repo says
    family: str | None
    gb: float
    # Whether dots runs it: None when this host can't tell
    runs: bool | None
    why: str | None = None
    # The model it was made from, as its card says: for pictures, what says how a Mac draws with it
    original: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def repo_of(link: str) -> str:
    """'https://huggingface.co/owner/name/tree/main' -> 'owner/name'"""
    text = _SITE.sub("", link.strip())
    repo = "/".join(text.split("/")[:2]).split("?")[0]
    if not _REPO.match(repo):
        raise ValueError("paste a Hugging Face model link, like huggingface.co/owner/model")
    return repo


def describe(link: str, hub=None) -> ModelDescription:
    """What the model at `link` is. `hub` stands in for huggingface_hub in tests."""
    if hub is None:
        import huggingface_hub as hub
    repo = repo_of(link)
    try:
        info = hub.HfApi().model_info(repo, files_metadata=True)
    except Exception as e:
        raise ValueError(f"couldn't read {repo} on Hugging Face: it may be private, gated or misspelled") from e
    files = {s.rfilename: (s.size or 0) for s in info.siblings}
    gb = round(sum(size for name, size in files.items() if name.endswith(".safetensors")) / 1e9, 1)
    from .catalog import short_name

    name = short_name(repo)
    tags = set(info.tags or [])

    if info.library_name == "mflux" or "mflux" in tags:
        from ..image_tools import runners

        original = _original(info)
        known = runners.for_original(original, runners.load()) is not None
        return ModelDescription(repo, name, "pictures", _pipeline_class(info, hub), gb, runs=None if known else False,
                                why=None if known else "dots has no way to draw with this kind of model yet",
                                original=original)
    if "config.json" in files and (info.pipeline_tag is None or info.pipeline_tag in _TEXT_TASKS):
        model_type = _read_json(hub, repo, "config.json").get("model_type")
        if model_type:
            runs = _engine_runs(model_type)
            why = None if runs is not False else f"dots doesn't run {model_type} models yet"
            return ModelDescription(repo, name, "text", model_type, gb, runs=runs, why=why)
    return ModelDescription(repo, name, "unknown", None, gb, runs=False,
                            why="dots can't tell what this model is from its files")


def _read_json(hub, repo: str, filename: str) -> dict:
    with open(hub.hf_hub_download(repo, filename)) as f:
        return json.load(f)


def _original(info) -> str | None:
    """The model a build was made from, as its card names it"""
    base = (info.card_data or {}).get("base_model") if info.card_data else None
    base = base[0] if isinstance(base, list) and base else base
    return base if isinstance(base, str) else None


def _pipeline_class(info, hub) -> str | None:
    """An image model's family: the pipeline class of the original it names, when that can be read."""
    base = _original(info)
    if base is None:
        return None
    try:
        return _read_json(hub, base, "model_index.json").get("_class_name")
    except Exception:
        return None  # gated, or not a diffusers repo


def _engine_runs(model_type: str) -> bool | None:
    """Whether the engine has this architecture. None where the engine isn't installed (a hub on Linux)."""
    if importlib.util.find_spec("mlx_lm") is None:
        return None
    return importlib.util.find_spec(f"mlx_lm.models.{model_type}") is not None

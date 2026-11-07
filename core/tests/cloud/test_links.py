"""Starting a pool from a link: what a Hugging Face repo runs, read from the repo itself."""

import json
from types import SimpleNamespace

import pytest

from dots.cloud.links import describe, repo_of


def test_links_in_every_form_name_the_same_repo():
    for link in ["https://huggingface.co/owner/model", "huggingface.co/owner/model/tree/main", "hf.co/owner/model?x=1",
                 "owner/model", "  https://www.huggingface.co/owner/model  "]:
        assert repo_of(link) == "owner/model"
    with pytest.raises(ValueError, match="Hugging Face model link"):
        repo_of("https://example.com/not a repo")


def _hub(tmp_path, repos: dict):
    """A stand-in for huggingface_hub: repo -> (library, pipeline tag, tags, files {name: size or json}, base model)"""
    def model_info(repo, files_metadata=True):
        if repo not in repos:
            raise FileNotFoundError(repo)
        library, task, tags, files, base = repos[repo]
        siblings = [SimpleNamespace(rfilename=name, size=v if isinstance(v, int) else 10) for name, v in files.items()]
        return SimpleNamespace(library_name=library, pipeline_tag=task, tags=tags, siblings=siblings,
                               card_data={"base_model": base} if base else None)

    def hf_hub_download(repo, filename):
        content = repos[repo][3][filename]
        path = tmp_path / f"{repo.replace('/', '_')}_{filename}"
        path.write_text(json.dumps(content))
        return str(path)

    return SimpleNamespace(HfApi=lambda: SimpleNamespace(model_info=model_info), hf_hub_download=hf_hub_download)


def test_text_pictures_and_what_dots_cant_tell(tmp_path):
    hub = _hub(tmp_path, {
        "a/chat-3B": ("transformers", "text-generation", ["mlx"],
                      {"config.json": {"model_type": "qwen3_5"}, "model.safetensors": 2_000_000_000}, None),
        "a/speech": ("transformers", "automatic-speech-recognition", [], {"config.json": {"model_type": "whisper"}}, None),
        "b/draw-q4": ("mflux", "text-to-image", ["mflux"], {"transformer/model.safetensors": 4_000_000_000}, "c/draw"),
        "c/draw": (None, None, [], {"model_index.json": {"_class_name": "DrawPipeline"}}, None),
        "d/mystery": (None, None, [], {"weights.bin": 5}, None),
    })
    text = describe("huggingface.co/a/chat-3B", hub)
    assert (text.kind, text.family, text.gb) == ("text", "qwen3_5", 2.0)
    pictures = describe("b/draw-q4", hub)
    assert (pictures.kind, pictures.family, pictures.gb) == ("pictures", "DrawPipeline", 4.0)
    for link in ("a/speech", "d/mystery"):
        unknown = describe(link, hub)
        assert unknown.kind == "unknown" and unknown.runs is False and unknown.why
    with pytest.raises(ValueError, match="private, gated or misspelled"):
        describe("e/missing", hub)

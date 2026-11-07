"""A Mac draws a pool's model only with a runner dots ships for its kind, never with a command from a job."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dots.image_tools import runners


def test_every_runner_names_its_originals_and_a_pinned_tool():
    loaded = runners.load()
    assert loaded and all(r.originals and r.draw for r in loaded)
    assert all(any("==" in word for word in r.draw) for r in loaded if r.style == "mflux"), "each mflux is pinned"
    assert all(r.files and all(ref.count("/") >= 2 for _, ref in r.files) for r in loaded if r.style == "sdcpp"), \
        "each stable-diffusion.cpp runner names the exact files it reads"
    flux2 = runners.for_original("black-forest-labs/FLUX.2-klein-4B", loaded)
    assert flux2 is not None and flux2.edit is not None
    driver = runners.for_original("black-forest-labs/flux-3-image-klein", loaded)
    assert driver is not None and "{tools}" not in " ".join(driver.draw) and driver.draw[-1].endswith("flux3_draw.py")
    assert runners.for_original("someone/unknown", loaded) is None and runners.for_original(None, loaded) is None


def test_a_job_names_only_a_model_and_the_runner_is_the_command():
    runner = runners.load()[0]
    tool = runner.tool("mflux-community/flux2-klein-4b-mflux-q4")
    assert tool.command == runner.draw and tool.model == "mflux-community/flux2-klein-4b-mflux-q4"
    for bad in ("--output=/etc/passwd", "../../x", "x; rm -rf /", "", "/abs/path"):
        with pytest.raises(ValueError):
            runner.tool(bad)


def test_a_runner_runs_only_on_a_new_enough_macos(monkeypatch):
    runner = runners.Runner("r", ("o",), "b", ("/bin/sh",), None, macos="14.0")
    monkeypatch.setattr(runners.sys, "platform", "darwin")
    monkeypatch.setattr(runners.platform, "mac_ver", lambda: ("13.4.1", ("", "", ""), ""))
    assert not runner.runs_here(), "macOS 13.4 is too old for it"
    monkeypatch.setattr(runners.platform, "mac_ver", lambda: ("15.1", ("", "", ""), ""))
    assert runner.runs_here()
    monkeypatch.setattr(runners.sys, "platform", "linux")
    assert not runner.runs_here()


def test_on_macos_13_flux2_klein_is_drawn_with_stable_diffusion_cpp(monkeypatch, tmp_path):
    monkeypatch.setattr(runners.sys, "platform", "darwin")
    monkeypatch.setattr(runners.platform, "mac_ver", lambda: ("13.4", ("", "", ""), ""))
    monkeypatch.setattr(runners, "dots_home", lambda: tmp_path)
    original = "black-forest-labs/FLUX.2-klein-4B"
    assert runners.for_original(original, runners.here()) is None, "not until its tool is built on this Mac"
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "sd-cli").write_text("#!/bin/sh\n")
    chosen = runners.for_original(original, runners.here())
    assert chosen is not None and chosen.style == "sdcpp" and chosen.draw == (str(tmp_path / "tools" / "sd-cli"),)
    monkeypatch.setattr(runners.platform, "mac_ver", lambda: ("15.1", ("", "", ""), ""))
    monkeypatch.setattr(runners.shutil, "which", lambda program: "/usr/local/bin/" + program)
    assert runners.for_original(original, runners.here()).style == "mflux", "MLX where it runs: it's faster"


def test_stable_diffusion_cpp_is_told_its_files_and_the_job():
    from dots.images import ImageJob, ImageTool

    tool = ImageTool(model="m/x", command=("/t/sd-cli",), edit_command=None, base_model=None, timeout_s=60, steps=4,
                     style="sdcpp", files=(("diffusion-model", "/c/klein.gguf"), ("llm", "/c/qwen.gguf")),
                     extra=("--cfg-scale", "1.0"))
    args = tool.arguments(ImageJob(prompt="a cat", width=512, height=512, seed=7), Path("/o/out.png"), None)
    assert args == ["/t/sd-cli", "--diffusion-model", "/c/klein.gguf", "--llm", "/c/qwen.gguf", "--cfg-scale", "1.0",
                    "-p", "a cat", "-o", "/o/out.png", "-W", "512", "-H", "512", "--steps", "4", "--seed", "7"]
    edit = tool.arguments(ImageJob(prompt="in winter", seed=1), Path("/o/out.png"), Path("/o/source.png"))
    assert edit[-2:] == ["-r", "/o/source.png"] and not tool.shows_steps


async def test_a_picture_comes_from_stable_diffusion_cpp(tmp_path):
    """A stand-in sd-cli that writes a PNG where -o says, as the real one does"""
    import io

    from PIL import Image

    from dots.images import ImageJob, ImageTool

    script = tmp_path / "sd-cli"
    script.write_text("""#!/usr/bin/env python3
import sys
from PIL import Image
args = sys.argv[1:]
Image.new("RGB", (int(args[args.index("-W") + 1]), int(args[args.index("-H") + 1])), "red").save(args[args.index("-o") + 1])
""".replace("#!/usr/bin/env python3", "#!" + sys.executable))
    script.chmod(0o755)
    tool = ImageTool(model="m/x", command=(str(script),), edit_command=None, base_model=None, timeout_s=60, style="sdcpp")
    steps = []

    async def on_step(*a):
        steps.append(a)

    png, ms = await tool.draw(ImageJob(prompt="a red square", width=64, height=48), on_step)
    assert Image.open(io.BytesIO(png)).size == (64, 48) and steps == [], "no previews: the picture comes whole"

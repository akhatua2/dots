"""A Mac draws a pool's model only with a runner dots ships for its kind, never with a command from a job."""

from __future__ import annotations

import pytest

from dots.image_tools import runners


def test_every_runner_names_its_originals_and_a_pinned_tool():
    loaded = runners.load()
    assert loaded and all(r.originals and r.draw and r.base_model for r in loaded)
    assert all(any("==" in word for word in r.draw) for r in loaded), "each tool's version is pinned"
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
    runner = runners.Runner("r", ("o",), "b", ("tool",), None, macos="14.0")
    monkeypatch.setattr(runners.sys, "platform", "darwin")
    monkeypatch.setattr(runners.platform, "mac_ver", lambda: ("13.4.1", ("", "", ""), ""))
    assert not runner.runs_here(), "macOS 13.4 is too old for it"
    monkeypatch.setattr(runners.platform, "mac_ver", lambda: ("15.1", ("", "", ""), ""))
    assert runner.runs_here()
    monkeypatch.setattr(runners.sys, "platform", "linux")
    assert not runner.runs_here()

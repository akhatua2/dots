"""Pictures: a job is checked at the door, sent whole to a Mac that offers an image model, and drawn by the
image tool that Mac runs. A stand-in tool plays mflux, which can rename what it writes."""

import base64
import sys
from types import SimpleNamespace

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from dots.app.api import HttpApi
from dots.distributed.hub import Hub, NoImageMac
from dots.images import ImageJob, ImageTool, parse_size

PNG = b"\x89PNG\r\n\x1a\n-a-picture"

# Writes PNG next to where it was asked, with a suffix, as mflux does when the name is taken
FAKE_TOOL = f"""
import sys, pathlib
args = sys.argv[1:]
out = pathlib.Path(args[args.index("--output") + 1])
if "--prompt" in args and args[args.index("--prompt") + 1] == "fail":
    print("out of memory"); sys.exit(1)
out.with_name(out.stem + "_1.png").write_bytes({PNG!r} + str(args).encode())
"""


def _tool(tmp_path, **kw) -> ImageTool:
    script = tmp_path / "tool.py"
    script.write_text(FAKE_TOOL)
    edit = (sys.executable, str(script), "--editing") if kw.get("edits", True) else None
    return ImageTool(model="klein", command=(sys.executable, str(script)), edit_command=edit, base_model="flux3-klein",
                     timeout_s=kw.get("timeout_s", 30))


def test_jobs_are_checked():
    assert parse_size("768x512") == (768, 512) and parse_size(None) == (None, None)
    with pytest.raises(ValueError, match="WIDTHxHEIGHT"):
        parse_size("big")
    with pytest.raises(ValueError, match="what to draw"):
        ImageJob(prompt=" ")
    with pytest.raises(ValueError, match="multiples"):
        ImageJob(prompt="a cat", width=1000)
    with pytest.raises(ValueError, match="between 0 and 1"):
        ImageJob(prompt="a cat", strength=2)


def test_the_tool_is_told_everything_and_a_starting_picture_only_for_edits(tmp_path):
    tool = _tool(tmp_path)
    plain = tool.arguments(ImageJob(prompt="a cat", width=512, height=768, steps=8, seed=3), tmp_path / "o.png", None)
    assert plain[plain.index("--width") + 1] == "512" and plain[plain.index("--height") + 1] == "768"
    assert plain[plain.index("--steps") + 1] == "8" and plain[plain.index("--seed") + 1] == "3"
    assert plain[plain.index("--base-model") + 1] == "flux3-klein" and "--editing" not in plain
    # An edit goes to the edit tool with the picture, and keeps the picture's size unless one is asked for
    edit = tool.arguments(ImageJob(prompt="make it winter", image=b"x"), tmp_path / "o.png", tmp_path / "s")
    assert "--editing" in edit and edit[edit.index("--image-paths") + 1] == str(tmp_path / "s") and "--width" not in edit
    # A job that doesn't say how many steps gets the worker's own
    fewer = ImageTool(**{**tool.__dict__, "steps": 8}).arguments(ImageJob(prompt="a cat"), tmp_path / "o.png", None)
    assert fewer[fewer.index("--steps") + 1] == "8"
    # Without an edit tool, the drawing tool starts from the picture
    restyle = _tool(tmp_path, edits=False).arguments(ImageJob(prompt="in winter", image=b"x", strength=0.6),
                                                     tmp_path / "o.png", tmp_path / "s")
    assert restyle[restyle.index("--image-path") + 1] == str(tmp_path / "s") and restyle[restyle.index("--image-strength") + 1] == "0.6"


async def test_a_picture_comes_back_even_when_the_tool_renames_it(tmp_path):
    png, ms = await _tool(tmp_path).draw(ImageJob(prompt="a cat", width=512, height=512))
    assert png.startswith(PNG) and b"--width" in png and ms > 0


async def test_a_failing_tool_says_why(tmp_path):
    with pytest.raises(RuntimeError, match="out of memory"):
        await _tool(tmp_path).draw(ImageJob(prompt="fail"))


async def test_the_hub_sends_a_picture_to_a_mac_that_offers_images_and_is_free():
    calls = []

    def worker(wid, images, busy=False):
        async def call(kind, **job):
            calls.append((wid, kind, job))
            return {"kind": "image_ok", "png": "cG5n", "model": images, "ms": 5.0}
        return SimpleNamespace(id=wid, short_id=wid, compute=True, images=images, busy=busy, conn=SimpleNamespace(call=call))

    pool = [worker("plain", None), worker("busy", "z", busy=True), worker("free", "z")]
    hub = SimpleNamespace(participants={w.id: w for w in pool}, is_serving=lambda wid: wid == "busy")
    drawn = await Hub.make_image(hub, prompt="a cat", width=512, height=512)
    assert drawn["worker_id"] == "free" and calls == [("free", "make_image", {"prompt": "a cat", "width": 512, "height": 512})]
    assert Hub.image_models(hub) == ["z"]
    with pytest.raises(NoImageMac):
        await Hub.make_image(SimpleNamespace(participants={"p": pool[0]}, is_serving=lambda _: False), prompt="x")


@pytest.fixture
async def api():
    jobs = []

    async def make_image(**job):
        jobs.append(job)
        return {"png": base64.b64encode(PNG).decode(), "model": "z", "ms": 1200.0, "worker_id": "w1"}

    http = HttpApi(SimpleNamespace(make_image=make_image))
    app = web.Application()
    app.router.add_post("/v1/images/generations", http._images)
    app.router.add_post("/v1/images/edits", http._images)
    async with TestClient(TestServer(app)) as client:
        yield client, jobs


async def test_generations_in_openai_shape(api):
    client, jobs = api
    r = await client.post("/v1/images/generations", json={"prompt": "a cat", "size": "512x768", "seed": 4})
    body = await r.json()
    assert r.status == 200 and base64.b64decode(body["data"][0]["b64_json"]) == PNG
    assert body["dots"]["model"] == "z" and jobs[0]["width"] == 512 and jobs[0]["height"] == 768 and jobs[0]["seed"] == 4
    bad = await client.post("/v1/images/generations", json={"prompt": "a cat", "size": "999x999"})
    assert bad.status == 400 and len(jobs) == 1


async def test_edits_take_the_picture_as_json_or_a_form(api):
    client, jobs = api
    start = b"starting picture"
    r = await client.post("/v1/images/edits", json={"prompt": "in winter", "size": "512x512", "strength": 0.6,
                                                     "image": "data:image/png;base64," + base64.b64encode(start).decode()})
    assert r.status == 200 and base64.b64decode(jobs[-1]["image"]) == start and jobs[-1]["strength"] == 0.6
    form = FormData()
    form.add_field("prompt", "in winter")
    form.add_field("size", "512x512")
    form.add_field("image", start, filename="road.png", content_type="image/png")
    r = await client.post("/v1/images/edits", data=form)
    assert r.status == 200 and base64.b64decode(jobs[-1]["image"]) == start
    missing = await client.post("/v1/images/edits", json={"prompt": "in winter"})
    assert missing.status == 400

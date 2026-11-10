"""Regenerate the README screenshots and demo GIF.

Drives the real twisk TUI against the in-memory demo backend (no server
needed), exports Textual SVG screenshots, renders them with Chromium via
Playwright and assembles the GIF with Pillow.

    uv sync --group docs
    uv run playwright install chromium     # once
    uv run python scripts/capture_screenshots.py

Outputs to docs/assets/. Set CHROMIUM_PATH to use an existing Chromium binary.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "assets"
SIZE = (150, 38)  # terminal columns x rows

# Keep the demo self-contained: never touch the real ~/.twisk. A throwaway
# HOME also makes spec paths render as ~/.twisk/specs/... like for a user.
_fake_home = tempfile.mkdtemp(prefix="twisk-shots-")
os.environ["HOME"] = _fake_home
_home = os.path.join(_fake_home, ".twisk")
os.environ["TWISK_HOME"] = _home

from textual.widgets import Input, TextArea  # noqa: E402

from twisk.backend.fake import FakeBackend  # noqa: E402
from twisk.specs.models import (  # noqa: E402
    ClusterSpec,
    JobSpec,
    PoolSpec,
    Resources,
    ServiceSpec,
    VolumeSpec,
)
from twisk.specs.store import SpecStore  # noqa: E402
from twisk.tui.app import TwiskApp  # noqa: E402


_KEYS = {":": "colon", "/": "slash", " ": "space", "_": "underscore"}

# The README's example team template (keep in sync with "Form templates" there).
GPU_BOX_TEMPLATE = """\
kind: cluster
title: GPU dev box
description: One node on our Kubernetes cluster, with our base image
fields:
  - {key: name, label: Cluster name, required: true}
  - key: gpu
    label: GPU
    type: select
    options: [L4, A100, H100]
    default: L4
  - {key: gpus, label: GPUs, type: int, default: 1, min: 1, max: 8}
  - {key: team, label: Team, pattern: "[a-z]+", help: lowercase team name}
  - {key: env, label: Environment, type: kv, help: "KEY=value, one per line"}
template: |
  name: {{ name }}
  resources:
    infra: k8s
    accelerators: {{ gpu }}:{{ gpus }}
    cpus: {{ gpus * 8 }}+
    memory: {{ gpus * 64 }}+
    image_id: docker:ghcr.io/acme/base:latest
    autostop: {idle_minutes: 120, down: true}
  envs: {{ env | yaml }}
  {% if team %}
  config:
    kubernetes:
      pod_config: {metadata: {labels: {team: {{ team | yaml }}}}}
  {% endif %}
"""


@dataclass
class Frame:
    svg: str
    seconds: float  # how long the frame stays up in the GIF


def seed_specs(store: SpecStore) -> None:
    store.save(ClusterSpec(name="dev-gpu", resources=Resources(infra="k8s", accelerators="L4:1", cpus="4", memory="16"),
                           setup="pip install -r requirements.txt", autostop_minutes=30, autostop_down=True))
    store.save(ClusterSpec(name="idle-h100", resources=Resources(infra="k8s", accelerators="H100:8")))
    store.save(JobSpec(name="nightly-eval", resources=Resources(infra="k8s", accelerators="A100:1"),
                       run="python eval.py --suite nightly", max_restarts_on_errors=2))
    store.save(ServiceSpec(name="llm-api", resources=Resources(infra="k8s", accelerators="L4:1"), port=8080,
                           run="vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8080", min_replicas=2))
    store.save(PoolSpec(name="eval-pool", resources=Resources(infra="k8s", cpus="2"), workers=2,
                        setup="pip install lm-eval"))
    store.save(VolumeSpec(name="datasets", size="100Gi"))


async def drive() -> tuple[dict[str, str], list[Frame]]:
    store = SpecStore(Path(_home) / "specs")
    seed_specs(store)
    app = TwiskApp(FakeBackend(speed=2), store=store, refresh_seconds=1, endpoint_source="demo", editor="")
    stills: dict[str, str] = {}
    frames: list[Frame] = []

    def grab(seconds: float, still: str | None = None) -> None:
        svg = app.export_screenshot()
        frames.append(Frame(svg, seconds))
        if still:
            stills[still] = svg

    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause(2.5)
        grab(3.0, "clusters")

        # Create a cluster through the form.
        await pilot.press("c")
        await pilot.pause(0.6)
        for ch in "train-llama":
            await pilot.press(ch)
        await pilot.pause(0.3)
        grab(1.2)
        screen = app.screen
        screen.query_one("#f-infra", Input).value = "k8s"
        screen.query_one("#f-accelerators", Input).value = "H100:8"
        screen.query_one("#f-cpus", Input).value = "32+"
        screen.query_one("#f-memory", Input).value = "256+"
        screen.query_one("#f-autostop_minutes", Input).value = "60"
        await pilot.pause(0.4)
        grab(1.2)
        screen.query_one("#f-autostop_down").value = True
        screen.query_one("#f-setup", TextArea).text = "pip install -r requirements.txt"
        screen.query_one("#f-run", TextArea).text = "torchrun --nproc_per_node 8 train.py --model llama-3.1-8b"
        await pilot.pause(0.5)
        grab(3.5, "create-form")

        # Launch: SkyPilot's output streams in.
        await pilot.press("ctrl+s")
        await pilot.pause(1.2)
        grab(1.2)
        await pilot.pause(2.5)
        grab(3.0, "launch-logs")
        await pilot.press("escape")
        await pilot.pause(2.5)
        grab(2.5)

        # Usage: GPU-hours and idle time.
        await pilot.press("7")
        await pilot.pause(2.0)
        grab(3.0, "usage")

        # Managed jobs, services, the spec library.
        await pilot.press("2")
        await pilot.pause(1.5)
        grab(2.0, "jobs")
        await pilot.press("colon")
        for ch in "svc":
            await pilot.press(ch)
        await pilot.pause(0.3)
        grab(1.0)
        await pilot.press("enter")
        await pilot.pause(2.5)
        grab(2.0, "services")
        await pilot.press("8")
        await pilot.pause(1.5)
        grab(3.0, "specs")

        # YAML escape hatch: the built-in editor on a stored spec.
        await pilot.press("1")
        await pilot.pause(1.5)
        table = app.screen.query_one("#table")
        table.move_cursor(row=table.get_row_index("dev-gpu"))
        await pilot.pause(0.2)
        await pilot.press("E")
        await pilot.pause(1.0)
        grab(1.5)
        # Add a top-level block the form doesn't cover, vim style.
        for key in ["G", "o", *"file_mounts:", "enter", "space", "space", *"/data: s3://team-datasets"]:
            await pilot.press(_KEYS.get(key, key))
        await pilot.pause(0.5)
        grab(2.5, "yaml-editor")
        await pilot.press("escape")
        for key in ":wq":
            await pilot.press(_KEYS.get(key, key))
        await pilot.pause(0.3)
        grab(1.0)
        await pilot.press("enter")
        await pilot.pause(1.5)
        grab(2.0)

        # Form templates: a team template next to the built-ins, and its form.
        (Path(_home) / "templates").mkdir(parents=True, exist_ok=True)
        (Path(_home) / "templates" / "gpu-box.yaml").write_text(GPU_BOX_TEMPLATE)
        await pilot.press("colon")
        for key in "templates":
            await pilot.press(key)
        await pilot.press("enter")
        await pilot.pause(1.5)
        table = app.screen.query_one("#table")
        table.move_cursor(row=table.get_row_index("gpu-box"))
        await pilot.pause(0.3)
        stills["templates"] = app.export_screenshot()
        await pilot.press("enter")
        await pilot.pause(0.6)
        for key in "train-box":
            await pilot.press(_KEYS.get(key, key))
        app.screen.query_one("#f-gpu").value = "H100"
        app.screen.query_one("#f-gpus").value = "4"
        app.screen.query_one("#f-team").value = "research"
        await pilot.pause(0.6)
        stills["custom-form"] = app.export_screenshot()
        await pilot.press("escape")
        await pilot.pause(0.3)
    return stills, frames


def render(svgs: list[str]) -> list[Image.Image]:
    images = []
    launch = {"executable_path": os.environ["CHROMIUM_PATH"]} if os.environ.get("CHROMIUM_PATH") else {}
    with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp:
        browser = p.chromium.launch(**launch)
        page = browser.new_page(device_scale_factor=1)
        for i, svg in enumerate(svgs):
            path = Path(tmp) / f"{i}.svg"
            path.write_text(svg)
            page.goto(path.as_uri())
            page.wait_for_timeout(100)
            png = page.query_selector("svg").screenshot()
            images.append(Image.open(io.BytesIO(png)).convert("RGB"))
        browser.close()
    return images


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    stills, frames = asyncio.run(drive())

    names = list(stills)
    for name, image in zip(names, render([stills[n] for n in names])):
        image.save(OUT / f"{name}.png", optimize=True)
        print(f"wrote docs/assets/{name}.png  {image.size[0]}x{image.size[1]}")

    images = render([f.svg for f in frames])
    # A shared adaptive palette keeps colors stable across frames and the file small.
    thumbs = [im.resize((im.width // 4, im.height // 4)) for im in images]
    sample = Image.new("RGB", (thumbs[0].width, sum(t.height for t in thumbs)))
    y = 0
    for t in thumbs:
        sample.paste(t, (0, y))
        y += t.height
    palette = sample.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    gif = [im.quantize(palette=palette, dither=Image.Dither.NONE) for im in images]
    gif[0].save(
        OUT / "twisk-demo.gif",
        save_all=True,
        append_images=gif[1:],
        duration=[int(f.seconds * 1000) for f in frames],
        loop=0,
        optimize=True,
    )
    size_kb = (OUT / "twisk-demo.gif").stat().st_size // 1024
    print(f"wrote docs/assets/twisk-demo.gif  {len(frames)} frames, {size_kb} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

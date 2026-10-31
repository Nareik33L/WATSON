"""Render scheduled frames to PNG + sidecar with a single headless Chromium."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from PIL import Image

from watson_fixturegen.cadence import ScheduledFrame, expand_schedule
from watson_fixturegen.ocr import OCR_JS, normalize_ocr_lines
from watson_fixturegen.paths import fonts_dir, templates_dir
from watson_fixturegen.phash import average_hash_hex
from watson_fixturegen.scenario import Scenario, load_scenario
from watson_fixturegen.sidecar import dump_sidecar, make_sidecar
from watson_fixturegen.truth import build_truth, dump_truth, pick_sample, write_sample_list

CHROMIUM_ARGS = (
    "--disable-gpu",
    "--hide-scrollbars",
    "--font-render-hinting=none",
    "--disable-font-subpixel-positioning",
    "--disable-lcd-text",
)
PALETTE_COLORS = 64
FIXTURE_ORIGIN = "https://fixture.watson.local/"
FONT_ROUTE = "https://fixture.watson.local/fonts/"


@dataclass(frozen=True)
class RenderResult:
    out_dir: Path
    frame_count: int
    truth_path: Path
    sample_path: Path
    frames_dir: Path
    manifest_path: Path | None


Progress = Callable[[int, int, str], None]


def render_scenario(
    scenario_path: Path,
    out_dir: Path,
    *,
    seed: int | None = None,
    dry_run: bool = False,
    progress: Progress | None = None,
) -> RenderResult:
    scenario = load_scenario(scenario_path)
    if seed is not None:
        scenario = _with_seed(scenario, seed)
    frames = expand_schedule(scenario)
    frames_dir = out_dir / "frames" / scenario.date
    truth = build_truth(scenario, frames)
    sample_ids = pick_sample(
        [frame.frame_id for frame in frames if not frame.window.excluded],
        50,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "truth.yaml").write_text(
        dump_truth({k: v for k, v in truth.items() if k != "_by_id"}),
        encoding="utf-8",
    )
    write_sample_list(out_dir / "sample50.txt", sample_ids)

    if dry_run:
        return RenderResult(
            out_dir=out_dir,
            frame_count=len(frames),
            truth_path=out_dir / "truth.yaml",
            sample_path=out_dir / "sample50.txt",
            frames_dir=frames_dir,
            manifest_path=None,
        )

    frames_root = out_dir / "frames"
    if frames_root.exists():
        shutil.rmtree(frames_root)
    frames_dir.mkdir(parents=True, exist_ok=True)
    _render_frames(scenario, frames, frames_dir, progress=progress)
    manifest_path = write_manifest(out_dir, scenario, frames, frames_dir)
    return RenderResult(
        out_dir=out_dir,
        frame_count=len(frames),
        truth_path=out_dir / "truth.yaml",
        sample_path=out_dir / "sample50.txt",
        frames_dir=frames_dir,
        manifest_path=manifest_path,
    )


def write_manifest(
    out_dir: Path,
    scenario: Scenario,
    frames: list[ScheduledFrame],
    frames_dir: Path,
) -> Path:
    entries: dict[str, dict[str, object]] = {}
    for frame in frames:
        png_path = frames_dir / f"{frame.frame_id}.png"
        with Image.open(png_path) as image:
            width, height = image.size
        rel = f"frames/{scenario.date}/{frame.frame_id}.png"
        entries[frame.frame_id] = {
            "png": rel,
            "sha256": hashlib.sha256(png_path.read_bytes()).hexdigest(),
            "width": width,
            "height": height,
        }
    payload = {
        "name": scenario.name,
        "date": scenario.date,
        "seed": scenario.seed,
        "frames": entries,
    }
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return path


def encode_fixture_png(image: Image.Image, path: Path) -> Image.Image:
    """Write a palette PNG8 and return the RGB pixels that were saved."""
    rgb = image.convert("RGB")
    palette = rgb.quantize(
        colors=PALETTE_COLORS,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )
    palette.save(path, format="PNG", optimize=True, compress_level=9)
    return Image.open(path).convert("RGB")


def _render_frames(
    scenario: Scenario,
    frames: list[ScheduledFrame],
    frames_dir: Path,
    *,
    progress: Progress | None,
) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - optional extra
        raise RuntimeError(
            "playwright is not installed. From the repo root: uv sync --group fixturegen"
        ) from exc

    bundled = fonts_dir()
    env = Environment(
        loader=FileSystemLoader(str(templates_dir())),
        undefined=StrictUndefined,
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=list(CHROMIUM_ARGS))
        context = browser.new_context(
            viewport={"width": scenario.width, "height": scenario.height},
            device_scale_factor=1,
            color_scheme="light",
            reduced_motion="reduce",
            has_touch=False,
            is_mobile=False,
            base_url=FIXTURE_ORIGIN,
        )
        page = context.new_page()
        page.set_viewport_size({"width": scenario.width, "height": scenario.height})
        page.emulate_media(color_scheme="light", reduced_motion="reduce")
        html_holder: list[str] = [""]
        page.route(
            f"{FIXTURE_ORIGIN}**",
            lambda route: _fulfill_fixture_request(route, html_holder, bundled),
        )
        try:
            for offset, frame in enumerate(frames, start=1):
                html_holder[0] = _render_html(env, scenario, frame)
                page.goto(FIXTURE_ORIGIN, wait_until="domcontentloaded")
                page.evaluate(
                    """() => document.fonts
                        ? document.fonts.load('16px WatsonFixture').then(() => document.fonts.ready)
                        : true"""
                )
                png_bytes = page.screenshot(
                    type="png",
                    full_page=False,
                    animations="disabled",
                    scale="device",
                )
                raw_lines = page.evaluate(OCR_JS)
                lines = normalize_ocr_lines(raw_lines)
                image = Image.open(BytesIO(png_bytes)).convert("RGB")
                if image.size != (scenario.width, scenario.height):
                    image = image.resize(
                        (scenario.width, scenario.height),
                        Image.Resampling.NEAREST,
                    )
                png_path = frames_dir / f"{frame.frame_id}.png"
                saved = encode_fixture_png(image, png_path)
                digest = average_hash_hex(saved)
                sidecar = make_sidecar(
                    frame_id=frame.frame_id,
                    captured_at=frame.captured_at,
                    display_id=scenario.display_id,
                    bundle_id=frame.window.bundle_id,
                    app_name=frame.window.app_name,
                    window_title=frame.window.window_title,
                    window_id=frame.window.window_id,
                    is_private_browsing=frame.window.is_private_browsing,
                    url=None,
                    ocr_lines=lines,
                    phash=digest,
                    width=scenario.width,
                    height=scenario.height,
                    capture_mode="fixture",
                )
                (frames_dir / f"{frame.frame_id}.json").write_text(
                    dump_sidecar(sidecar), encoding="utf-8"
                )
                if progress is not None:
                    progress(offset, len(frames), frame.frame_id)
        finally:
            context.close()
            browser.close()


def _fulfill_fixture_request(route: Any, html_holder: list[str], bundled: Path) -> None:
    url = str(route.request.url)
    if url.startswith(FONT_ROUTE):
        path = bundled / url.rsplit("/", 1)[-1]
        if path.is_file():
            route.fulfill(path=str(path), content_type="font/ttf")
            return
        route.abort()
        return
    route.fulfill(body=html_holder[0], content_type="text/html; charset=utf-8")


def _render_html(env: Environment, scenario: Scenario, frame: ScheduledFrame) -> str:
    template = env.get_template(f"{frame.window.template}.html")
    return template.render(
        width=scenario.width,
        height=scenario.height,
        app_name=frame.window.app_name,
        window_title=frame.window.window_title,
        url=frame.window.url or "",
        clock=frame.captured_at.strftime("%H:%M"),
        date_label=frame.captured_at.strftime("%a %d %b"),
        content=frame.content,
    )


def _with_seed(scenario: Scenario, seed: int) -> Scenario:
    return Scenario(
        name=scenario.name,
        date=scenario.date,
        seed=seed,
        display_id=scenario.display_id,
        width=scenario.width,
        height=scenario.height,
        tzinfo=scenario.tzinfo,
        cadence=scenario.cadence,
        windows=scenario.windows,
        timeline=scenario.timeline,
        truth=scenario.truth,
        source_path=scenario.source_path,
    )

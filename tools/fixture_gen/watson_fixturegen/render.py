"""Render scheduled frames to PNG + sidecar with a single headless Chromium."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from PIL import Image

from watson_fixturegen.cadence import ScheduledFrame, expand_schedule
from watson_fixturegen.ocr import OCR_JS, normalize_ocr_lines
from watson_fixturegen.paths import templates_dir
from watson_fixturegen.phash import dhash_hex
from watson_fixturegen.scenario import Scenario, load_scenario
from watson_fixturegen.sidecar import dump_sidecar, make_sidecar
from watson_fixturegen.truth import build_truth, dump_truth, pick_sample, write_sample_list

CHROMIUM_ARGS = (
    "--disable-gpu",
    "--hide-scrollbars",
    "--font-render-hinting=none",
    "--disable-font-subpixel-positioning",
)


@dataclass(frozen=True)
class RenderResult:
    out_dir: Path
    frame_count: int
    truth_path: Path
    sample_path: Path
    frames_dir: Path


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
    sample_ids = pick_sample([frame.frame_id for frame in frames], 50)

    if dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "truth.yaml").write_text(
            dump_truth({k: v for k, v in truth.items() if k != "_by_id"}),
            encoding="utf-8",
        )
        write_sample_list(out_dir / "sample50.txt", sample_ids)
        return RenderResult(
            out_dir=out_dir,
            frame_count=len(frames),
            truth_path=out_dir / "truth.yaml",
            sample_path=out_dir / "sample50.txt",
            frames_dir=frames_dir,
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    _render_frames(scenario, frames, frames_dir, progress=progress)
    truth_path = out_dir / "truth.yaml"
    truth_path.write_text(dump_truth(truth), encoding="utf-8")
    sample_path = out_dir / "sample50.txt"
    write_sample_list(sample_path, sample_ids)
    return RenderResult(
        out_dir=out_dir,
        frame_count=len(frames),
        truth_path=truth_path,
        sample_path=sample_path,
        frames_dir=frames_dir,
    )


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
        )
        page = context.new_page()
        page.emulate_media(color_scheme="light", reduced_motion="reduce")
        try:
            for offset, frame in enumerate(frames, start=1):
                html = _render_html(env, scenario, frame)
                page.set_content(html, wait_until="domcontentloaded")
                page.evaluate("() => document.fonts ? document.fonts.ready : true")
                png_bytes = page.screenshot(type="png", full_page=False, animations="disabled")
                raw_lines = page.evaluate(OCR_JS)
                lines = normalize_ocr_lines(raw_lines)
                image = Image.open(BytesIO(png_bytes)).convert("RGB")
                if image.size != (scenario.width, scenario.height):
                    image = image.resize(
                        (scenario.width, scenario.height),
                        Image.Resampling.NEAREST,
                    )
                digest = dhash_hex(image)
                png_path = frames_dir / f"{frame.frame_id}.png"
                image.save(png_path, format="PNG", optimize=True, compress_level=9)
                sidecar = make_sidecar(
                    frame_id=frame.frame_id,
                    captured_at=frame.captured_at,
                    display_id=scenario.display_id,
                    bundle_id=frame.window.bundle_id,
                    app_name=frame.window.app_name,
                    window_title=frame.window.window_title,
                    window_id=frame.window.window_id,
                    is_private_browsing=frame.window.is_private_browsing,
                    url=frame.window.url,
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

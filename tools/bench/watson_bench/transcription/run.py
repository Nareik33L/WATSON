"""Run the A0 transcription bake-off. Measured passes stay offline."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from watson.privacy.network import localhost_only
from watson_bench.hardware import HardwareInfo, detect_hardware
from watson_bench.metrics import RssSampler, timed
from watson_bench.paths import default_models_dir, default_results_dir
from watson_bench.transcription.audio import MeetingFixture, discover_meeting_fixtures
from watson_bench.transcription.fetch import fetch_transcription_model
from watson_bench.transcription.protocol import (
    DEFAULT_ENGINES,
    DEFAULT_SIZES,
    TRANSCRIBE_ENGINES,
    TRANSCRIBE_SIZES,
    get_transcription_runtime,
)
from watson_bench.transcription.report import (
    default_transcription_report_path,
    write_transcription_report,
)
from watson_bench.transcription.wer import hallucination, join_segment_text, word_error_rate


def parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def normalize_engines(values: list[str] | None) -> list[str]:
    engines = list(values or DEFAULT_ENGINES)
    unknown = [name for name in engines if name not in TRANSCRIBE_ENGINES]
    if unknown:
        raise ValueError(f"unknown engines: {', '.join(unknown)}")
    return engines


def normalize_sizes(values: list[str] | None) -> list[str]:
    sizes = list(values or DEFAULT_SIZES)
    unknown = [name for name in sizes if name not in TRANSCRIBE_SIZES]
    if unknown:
        raise ValueError(f"unknown sizes: {', '.join(unknown)}")
    return sizes


def default_meeting_fixtures_dir() -> Path:
    override = os.environ.get("WATSON_MEETING_FIXTURES_DIR")
    if override:
        return Path(override).resolve()
    from watson_bench.paths import repo_root

    return repo_root() / "tests" / "fixtures" / "meetings"


def fetch_transcription(
    *,
    engines: list[str],
    sizes: list[str],
    models_dir: Path | None = None,
) -> list[str]:
    models_dir = models_dir or default_models_dir()
    messages: list[str] = []
    for engine in engines:
        for size in sizes:
            messages.append(fetch_transcription_model(engine, size, models_dir))
    return messages


def run_transcription(
    *,
    profile: str,
    engines: list[str] | None = None,
    sizes: list[str] | None = None,
    fixtures_dir: Path | None = None,
    out_dir: Path | None = None,
    models_dir: Path | None = None,
    report_path: Path | None = None,
    language_hint: str | None = "en",
    hardware: HardwareInfo | None = None,
) -> Path:
    hardware = hardware or detect_hardware()
    engines = normalize_engines(engines)
    sizes = normalize_sizes(sizes)
    fixtures_dir = fixtures_dir or default_meeting_fixtures_dir()
    models_dir = models_dir or default_models_dir()
    fixtures = discover_meeting_fixtures(fixtures_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = out_dir or (default_results_dir() / f"transcribe-{profile}-{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    results: list[dict[str, Any]] = []
    with localhost_only(
        error_prefix="Transcription bench",
        lookup_hint="Models are downloaded only by `watson-bench transcribe --fetch-only`.",
        connect_hint="Models are downloaded only by `watson-bench transcribe --fetch-only`.",
    ):
        for engine in engines:
            for size in sizes:
                results.append(
                    _run_one(
                        engine=engine,
                        size=size,
                        fixtures=fixtures,
                        models_dir=models_dir,
                        language_hint=language_hint,
                    )
                )

    manifest = {
        "started_at": stamp,
        "finished_at": datetime.now(UTC).isoformat(),
        "kind": "transcription",
        "profile": profile,
        "hardware": hardware.to_dict(),
        "engines": engines,
        "sizes": sizes,
        "fixtures_dir": str(fixtures_dir),
        "fixture_count": len(fixtures),
        "results": results,
    }
    (out_dir / "run.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if report_path is not None:
        dest = (
            default_transcription_report_path()
            if str(report_path) == "auto"
            else report_path
        )
        write_transcription_report(
            dest,
            hardware=hardware,
            profile=profile,
            results=results,
            fixtures_dir=str(fixtures_dir),
            fixture_count=len(fixtures),
        )
    return out_dir


def _run_one(
    *,
    engine: str,
    size: str,
    fixtures: list[MeetingFixture],
    models_dir: Path,
    language_hint: str | None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "engine": engine,
        "size": size,
        "skipped": False,
        "skip_reason": None,
        "error": None,
        "load_time_s": None,
        "realtime_factor": None,
        "wer": None,
        "peak_rss_bytes": None,
        "audio_s": None,
        "wall_s": None,
        "n_fixtures": 0,
        "hallucination": {},
        "fixtures": [],
    }
    try:
        runtime = get_transcription_runtime(engine)
    except ValueError as exc:
        row["skipped"] = True
        row["skip_reason"] = str(exc)
        return row
    skip = runtime.availability(size, models_dir)
    if skip is not None:
        row["skipped"] = True
        row["skip_reason"] = skip.reason
        return row

    sampler = RssSampler()
    sampler.start()
    try:
        with timed() as load_elapsed:
            runtime.load(size, models_dir)
        row["load_time_s"] = load_elapsed()
        _measure_fixtures(runtime, fixtures, language_hint, row)
    except (OSError, ValueError, RuntimeError) as exc:
        row["error"] = str(exc)
    finally:
        try:
            runtime.unload()
        except Exception:
            pass
        row["peak_rss_bytes"] = sampler.stop()
    return row


def _measure_fixtures(
    runtime: object,
    fixtures: list[MeetingFixture],
    language_hint: str | None,
    row: dict[str, Any],
) -> None:
    from watson_bench.transcription.adapters import FakeTranscriptionRuntime

    audio_s = 0.0
    wall_s = 0.0
    wers: list[float] = []
    details: list[dict[str, Any]] = []
    probes: dict[str, Any] = {}

    for fixture in fixtures:
        item: dict[str, Any] = {
            "name": fixture.name,
            "kind": fixture.kind,
            "duration_s": fixture.duration_s,
        }
        if fixture.is_decode_probe:
            item["skipped"] = True
            item["skip_reason"] = "decode/unsupported probe is not transcribed in A0"
            details.append(item)
            continue
        if fixture.load_error or fixture.pcm is None:
            item["skipped"] = True
            item["skip_reason"] = fixture.load_error or "no 16 kHz WAV"
            details.append(item)
            continue
        if isinstance(runtime, FakeTranscriptionRuntime):
            runtime.echo_text = fixture.reference_text
        with timed() as elapsed:
            segments = runtime.transcribe(fixture.pcm, language_hint)  # type: ignore[attr-defined]
        wall = elapsed()
        audio_s += fixture.duration_s
        wall_s += wall
        hyp = join_segment_text(segments)
        wer = None
        if fixture.reference_text or hyp:
            wer = word_error_rate(fixture.reference_text, hyp)
        if wer is not None and not fixture.is_hallucination_probe:
            wers.append(wer)
        if fixture.is_hallucination_probe:
            probes[fixture.name] = hallucination(segments)
        item.update(
            {
                "wall_s": wall,
                "realtime_factor": (wall / fixture.duration_s) if fixture.duration_s else None,
                "wer": wer,
                "n_segments": len(segments),
                "text": hyp,
            }
        )
        details.append(item)

    row["n_fixtures"] = len(details)
    row["audio_s"] = audio_s
    row["wall_s"] = wall_s
    row["realtime_factor"] = (wall_s / audio_s) if audio_s else None
    row["wer"] = (sum(wers) / len(wers)) if wers else None
    row["hallucination"] = probes
    row["fixtures"] = details

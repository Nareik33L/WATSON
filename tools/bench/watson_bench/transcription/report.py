"""Write docs/benchmarks/<date>-transcription.md like the M0-A report."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from watson_bench.hardware import HardwareInfo, format_hardware
from watson_bench.paths import repo_root
from watson_bench.report import _fmt, _fmt_bytes


def default_transcription_report_path(date_stamp: str | None = None) -> Path:
    stamp = date_stamp or date.today().isoformat()
    return repo_root() / "docs" / "benchmarks" / f"{stamp}-transcription.md"


def write_transcription_report(
    path: Path,
    *,
    hardware: HardwareInfo,
    profile: str,
    results: list[dict[str, Any]],
    fixtures_dir: str,
    fixture_count: int,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_transcription_report(
            hardware=hardware,
            profile=profile,
            results=results,
            fixtures_dir=fixtures_dir,
            fixture_count=fixture_count,
        ),
        encoding="utf-8",
    )
    return path


def render_transcription_report(
    *,
    hardware: HardwareInfo,
    profile: str,
    results: list[dict[str, Any]],
    fixtures_dir: str,
    fixture_count: int,
) -> str:
    lines = [
        f"# WATSON A0 transcription bake-off — {date.today().isoformat()}",
        "",
        "Harness-generated report. **Engine/size pairs are candidates, not chosen defaults.**",
        "A human fills Recommendation after the development-Mac run (plan A0 / A5).",
        "",
        "Informs V1.1 open decision 1: whisper.cpp vs mlx-whisper; sizes per RAM tier.",
        "",
        "## Hardware",
        "",
        f"- {format_hardware(hardware)}",
        f"- RAM profile requested: `{profile}`",
        f"- Fixtures: {fixture_count} from `{fixtures_dir}`",
        "",
        "## Results",
        "",
        _results_table(results),
        "",
        "## Hallucination probes (silence / tone)",
        "",
        _hallucination_table(results),
        "",
        "## Skips and errors",
        "",
    ]
    skips = [row for row in results if row.get("skipped") or row.get("error")]
    if skips:
        for row in skips:
            ident = f"{row.get('engine')}/{row.get('size')}"
            if row.get("skipped"):
                lines.append(f"- `{ident}` skipped: {row.get('skip_reason')}")
            if row.get("error"):
                lines.append(f"- `{ident}` error: {row.get('error')}")
    else:
        lines.append("None.")
    lines.extend(
        [
            "",
            "## Recommendation",
            "",
            "_Left for the human/lead to fill after the Mac run._",
            "",
            "Review load time, real-time factor, WER on the 12-minute stand-up,",
            "peak RSS, and whether silence/tone probes stay empty.",
            "",
            "| Profile | Chosen engine | Size | Rationale |",
            "| --- | --- | --- | --- |",
            "| 8gb | _tbd_ |  |  |",
            "| 16gb | _tbd_ |  |  |",
            "",
            "Package transcription runtime (pywhispercpp vs mlx-whisper): _tbd_",
            "",
        ]
    )
    return "\n".join(lines)


def _results_table(results: list[dict[str, Any]]) -> str:
    header = "| Engine | Size | Status | load s | RTF | WER | peak RSS | audio s | wall s |"
    sep = "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"
    rows = [header, sep]
    for row in results:
        status = "skipped" if row.get("skipped") else ("error" if row.get("error") else "ok")
        rss = row.get("peak_rss_bytes")
        rss_cell = _fmt_bytes(rss) if isinstance(rss, int) else "—"
        rows.append(
            "| "
            + " | ".join(
                [
                    str(row.get("engine") or ""),
                    str(row.get("size") or ""),
                    status,
                    _fmt(row.get("load_time_s")),
                    _fmt(row.get("realtime_factor")),
                    _fmt_wer(row.get("wer")),
                    rss_cell,
                    _fmt(row.get("audio_s")),
                    _fmt(row.get("wall_s")),
                ]
            )
            + " |"
        )
    return "\n".join(rows)


def _hallucination_table(results: list[dict[str, Any]]) -> str:
    header = "| Engine | Size | silence | tone |"
    sep = "| --- | --- | --- | --- |"
    rows = [header, sep]
    any_probe = False
    for row in results:
        probes = row.get("hallucination") or {}
        if not probes and not row.get("skipped"):
            continue
        any_probe = True
        silence = probes.get("silence_only") or probes.get("silence") or {}
        tone = probes.get("tone_only") or probes.get("tone") or {}
        rows.append(
            "| "
            + " | ".join(
                [
                    str(row.get("engine") or ""),
                    str(row.get("size") or ""),
                    _hallucination_cell(silence),
                    _hallucination_cell(tone),
                ]
            )
            + " |"
        )
    if not any_probe:
        return "No silence/tone fixtures were in this run."
    return "\n".join(rows)


def _hallucination_cell(probe: object) -> str:
    if not isinstance(probe, dict):
        return "—"
    if probe.get("skipped"):
        return "skipped"
    if probe.get("hallucinated"):
        return f"FAIL ({probe.get('n_nonempty_segments')})"
    if "hallucinated" in probe:
        return "empty"
    return "—"


def _fmt_wer(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value * 100:.1f}%"
    return str(value)

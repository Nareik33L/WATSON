"""Write docs/benchmarks/<date>.md with a results table and a blank Recommendation."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from watson_bench.hardware import HardwareInfo, format_hardware
from watson_bench.paths import default_report_path


def report_path_for_today(today: date | None = None) -> Path:
    stamp = (today or date.today()).isoformat()
    return default_report_path(stamp)


def write_report(
    path: Path,
    *,
    hardware: HardwareInfo,
    profile: str,
    results: list[dict[str, Any]],
    frames_dir: str,
    frame_count: int,
    scoring_csv: str | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_report(
            hardware=hardware,
            profile=profile,
            results=results,
            frames_dir=frames_dir,
            frame_count=frame_count,
            scoring_csv=scoring_csv,
        ),
        encoding="utf-8",
    )
    return path


def render_report(
    *,
    hardware: HardwareInfo,
    profile: str,
    results: list[dict[str, Any]],
    frames_dir: str,
    frame_count: int,
    scoring_csv: str | None = None,
) -> str:
    lines = [
        f"# WATSON M0-A bake-off — {date.today().isoformat()}",
        "",
        "Harness-generated report. **Candidate IDs are not chosen defaults.**",
        "A human fills Recommendation after reviewing timings and the hand-scoring sheet.",
        "",
        "Informs §14 open decisions:",
        "",
        "1. Package runtime: llama.cpp vs MLX",
        "2. Default VLM / LLM / embedding models and quantisations",
        "",
        "## Hardware",
        "",
        f"- {format_hardware(hardware)}",
        f"- RAM profile requested: `{profile}`",
        f"- Frames: {frame_count} from `{frames_dir}`",
    ]
    if scoring_csv:
        lines.append(f"- Hand-scoring sheet: `{scoring_csv}`")
    lines.extend(["", "## Results", "", _results_table(results), "", "## Skips and errors", ""])
    skips = [row for row in results if row.get("skipped") or row.get("error")]
    if skips:
        for row in skips:
            candidate = row.get("candidate") or {}
            ident = candidate.get("id", "?")
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
            "Review `tokens/s`, time per frame (VLM), time per call (LLM), embedding throughput,",
            "peak RSS, load time, and the hand-scoring sheet on 50 sampled frames.",
            "",
            "| Role | Chosen candidate | Runtime | Quantisation | Rationale |",
            "| --- | --- | --- | --- | --- |",
            "| VLM | _tbd_ |  |  |  |",
            "| LLM | _tbd_ |  |  |  |",
            "| Embeddings | _tbd_ |  |  |  |",
            "",
            "16 GB fallback (if this run used the 8 GB profile): _tbd_",
            "",
            "Package runtime (llama.cpp vs MLX): _tbd_",
            "",
        ]
    )
    return "\n".join(lines)


def load_run_manifest(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "run.json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} is not a JSON object")
    return loaded


def _results_table(results: list[dict[str, Any]]) -> str:
    header = (
        "| Role | Candidate | Family | Runtime | Quant | Status | "
        "tokens/s | s/frame (VLM) | s/call (LLM) | emb texts/s | peak RSS | load s |"
    )
    sep = "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"
    rows = [header, sep]
    for row in results:
        candidate = row.get("candidate") or {}
        status = "skipped" if row.get("skipped") else ("error" if row.get("error") else "ok")
        rss = row.get("peak_rss_bytes")
        rss_cell = _fmt_bytes(rss) if isinstance(rss, int) else "—"
        rows.append(
            "| "
            + " | ".join(
                [
                    str(candidate.get("role") or ""),
                    f"`{candidate.get('id') or ''}`",
                    str(candidate.get("family") or ""),
                    str(candidate.get("runtime") or ""),
                    str(candidate.get("quantisation") or ""),
                    status,
                    _fmt(row.get("tokens_per_s")),
                    _fmt(row.get("time_per_frame_s")),
                    _fmt(row.get("time_per_call_s")),
                    _fmt(row.get("embedding_texts_per_s")),
                    rss_cell,
                    _fmt(row.get("load_time_s")),
                ]
            )
            + " |"
        )
    return "\n".join(rows)


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if value == 0:
            return "0"
        if abs(value) < 0.01:
            return f"{value:.4f}"
        return f"{value:.2f}"
    return str(value)


def _fmt_bytes(value: int) -> str:
    if value >= 1024**3:
        return f"{value / 1024**3:.2f} GiB"
    if value >= 1024**2:
        return f"{value / 1024**2:.0f} MiB"
    return f"{value} B"

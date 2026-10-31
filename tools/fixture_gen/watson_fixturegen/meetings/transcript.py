"""Reference transcripts with timestamps, plus truth.yaml dumps."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from watson_fixturegen.meetings.script import MeetingScript
from watson_fixturegen.truth import TruthDumper


def format_ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes:02d}:{rest:06.3f}"


def format_transcript_line(start_s: float, end_s: float, speaker: str, text: str) -> str:
    return f"[{format_ts(start_s)}-{format_ts(end_s)}] {speaker}: {text}"


def render_transcript(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in rows:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        lines.append(
            format_transcript_line(
                float(row["start_s"]),
                float(row["end_s"]),
                str(row.get("speaker") or "unknown"),
                text,
            )
        )
    return ("\n".join(lines) + "\n") if lines else ""


def spoken_from_transcript(text: str) -> str:
    """Strip timestamps and speaker labels for WER."""
    parts: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "] " in stripped and stripped.startswith("["):
            after = stripped.split("] ", 1)[1]
            if ": " in after:
                after = after.split(": ", 1)[1]
            parts.append(after)
            continue
        parts.append(stripped)
    return " ".join(parts)


def dump_meeting_truth(script: MeetingScript, *, duration_ms: int | None = None) -> str:
    payload: dict[str, Any] = {
        "name": script.name,
        "kind": script.kind,
        "title": script.title,
        "date": script.date,
        "language": script.language,
        "participants": list(script.truth.participants),
        "decisions": [dict(item) for item in script.truth.decisions],
        "commitments": [dict(item) for item in script.truth.commitments],
        "project_updates": [dict(item) for item in script.truth.project_updates],
        "discussion_points": [dict(item) for item in script.truth.discussion_points],
        "traps": [dict(item) for item in script.truth.traps],
    }
    if script.truth.notes:
        payload["notes"] = script.truth.notes
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    payload["meta"] = {
        "script": str(script.source_path.name),
        "sample_rate": script.sample_rate,
    }
    return yaml.dump(
        payload,
        Dumper=TruthDumper,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

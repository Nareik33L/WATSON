"""Build §11.1 truth.yaml plus D3 expected segments from a frame schedule."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import yaml

from watson_fixturegen.cadence import ScheduledFrame, normalize_title
from watson_fixturegen.scenario import Scenario
from watson_fixturegen.sidecar import format_captured_at


class TruthDumper(yaml.SafeDumper):
    """Stable YAML: insertion order, short string lists in flow style."""


def _represent_str_list(dumper: TruthDumper, data: list[Any]) -> yaml.Node:
    if data and all(isinstance(item, str) for item in data) and len(data) <= 16:
        return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=False)


TruthDumper.add_representer(list, _represent_str_list)


def build_truth(scenario: Scenario, frames: Sequence[ScheduledFrame]) -> dict[str, Any]:
    if not frames:
        raise ValueError("cannot build truth for an empty schedule")
    by_id = {frame.frame_id: frame for frame in frames}
    decisions = []
    for item in scenario.truth.decisions:
        text = str(item["text"])
        evidence = _resolve_evidence(item, frames)
        decisions.append({"text": text, "evidence_frames": evidence})
    commitments = []
    for item in scenario.truth.commitments:
        row = {"text": str(item["text"]), "status": str(item.get("status", "open"))}
        commitments.append(row)
    payload: dict[str, Any] = {
        "projects": list(scenario.truth.projects),
        "people": list(scenario.truth.people),
        "decisions": decisions,
        "commitments": commitments,
        "completed": list(scenario.truth.completed),
        "not_completed": list(scenario.truth.not_completed),
        "noise": list(scenario.truth.noise),
    }
    skipped = skipped_frames(frames)
    if skipped:
        payload["skipped"] = skipped
    payload["expected_segments"] = expected_segments(frames)
    payload["meta"] = {
        "name": scenario.name,
        "date": scenario.date,
        "seed": scenario.seed,
        "frame_count": len(frames),
        "first_frame": frames[0].frame_id,
        "last_frame": frames[-1].frame_id,
    }
    payload["_by_id"] = by_id
    return payload


def dump_truth(truth: dict[str, Any]) -> str:
    payload = {key: value for key, value in truth.items() if not key.startswith("_")}
    return yaml.dump(
        payload,
        Dumper=TruthDumper,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )


def skipped_windows(scenario: Scenario) -> list[dict[str, Any]]:
    """Timeline blocks marked excluded (used by tests that inspect the scenario)."""
    rows: list[dict[str, Any]] = []
    for block in scenario.timeline:
        if not block.window.excluded:
            continue
        rows.append(
            {
                "reason": "excluded_app",
                "window": block.window.key,
                "bundle_id": block.window.bundle_id,
                "app_name": block.window.app_name,
                "window_title": block.window.window_title,
                "start": format_captured_at(block.start),
                "end": format_captured_at(block.end),
            }
        )
    return rows


def skipped_frames(frames: Sequence[ScheduledFrame]) -> list[dict[str, Any]]:
    """One truth row per generated excluded frame (audit count must match)."""
    return [
        {
            "reason": "excluded_app",
            "frame_id": frame.frame_id,
            "bundle_id": frame.window.bundle_id,
            "captured_at": format_captured_at(frame.captured_at),
        }
        for frame in frames
        if frame.window.excluded
    ]


def expected_segments(frames: Sequence[ScheduledFrame]) -> list[dict[str, Any]]:
    """Maximal consecutive runs of (bundle_id, normalised window title) — D3."""
    segments: list[dict[str, Any]] = []
    current: list[ScheduledFrame] = []
    current_key: tuple[str, str] | None = None
    for frame in frames:
        if frame.window.excluded:
            continue
        key = (frame.window.bundle_id, normalize_title(frame.window.window_title))
        if current_key is None:
            current_key = key
            current = [frame]
            continue
        if key == current_key:
            current.append(frame)
            continue
        segments.append(_segment_row(current))
        current_key = key
        current = [frame]
    if current:
        segments.append(_segment_row(current))
    return segments


def pick_sample(frame_ids: Sequence[str], count: int = 50) -> list[str]:
    if count <= 0:
        return []
    if len(frame_ids) <= count:
        return list(frame_ids)
    if count == 1:
        return [frame_ids[0]]
    chosen: list[str] = []
    last_index = -1
    for step in range(count):
        index = round(step * (len(frame_ids) - 1) / (count - 1))
        if index <= last_index:
            index = last_index + 1
        if index >= len(frame_ids):
            index = len(frame_ids) - 1
        chosen.append(frame_ids[index])
        last_index = index
    return chosen


def write_sample_list(path: Any, frame_ids: Sequence[str]) -> None:
    lines = "# M0-A hand-scored quality sample (evenly spaced frame ids)\n"
    lines += "\n".join(frame_ids) + "\n"
    path.write_text(lines, encoding="utf-8")


def _segment_row(frames: Sequence[ScheduledFrame]) -> dict[str, Any]:
    first, last = frames[0], frames[-1]
    return {
        "start": format_captured_at(first.captured_at),
        "end": format_captured_at(last.captured_at),
        "start_frame": first.frame_id,
        "end_frame": last.frame_id,
        "fixture_index": [first.index, last.index],
        "bundle_id": first.window.bundle_id,
        "window_title": first.window.window_title,
        "frame_ids": [frame.frame_id for frame in frames],
    }


def _resolve_evidence(item: dict[str, Any], frames: Sequence[ScheduledFrame]) -> list[str]:
    if "evidence_frames" in item:
        return [str(frame_id) for frame_id in item["evidence_frames"]]
    needle = str(item.get("evidence_contains") or "").strip()
    if not needle:
        raise ValueError(f"decision/commitment {item!r} needs evidence_contains or evidence_frames")
    matched = [frame.frame_id for frame in frames if needle in frame.corpus]
    if not matched:
        raise ValueError(f"no frames contain evidence {needle!r}")
    return matched

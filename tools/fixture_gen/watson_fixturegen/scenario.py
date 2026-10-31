"""Load and validate a fixture-day scenario YAML."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

TEMPLATES = frozenset(
    {"mail", "document", "chat", "spreadsheet", "browser", "ide", "videocall"}
)


@dataclass(frozen=True)
class Cadence:
    default_seconds: int = 45
    stretch_seconds: int = 120
    tighten_seconds: int = 30
    stretch_after_stable: int = 3


@dataclass(frozen=True)
class WindowSpec:
    key: str
    template: str
    bundle_id: str
    app_name: str
    window_title: str
    window_id: int
    url: str | None = None
    is_private_browsing: bool = False
    excluded: bool = False


@dataclass(frozen=True)
class ContentState:
    at: datetime
    content: dict[str, Any]


@dataclass(frozen=True)
class TimelineBlock:
    window: WindowSpec
    start: datetime
    end: datetime
    states: tuple[ContentState, ...]


@dataclass(frozen=True)
class TruthSpec:
    projects: tuple[str, ...]
    people: tuple[str, ...]
    decisions: tuple[dict[str, Any], ...]
    commitments: tuple[dict[str, Any], ...]
    completed: tuple[str, ...]
    not_completed: tuple[str, ...]
    noise: tuple[str, ...]


@dataclass(frozen=True)
class Scenario:
    name: str
    date: str
    seed: int
    display_id: int
    width: int
    height: int
    tzinfo: timezone
    cadence: Cadence
    windows: dict[str, WindowSpec]
    timeline: tuple[TimelineBlock, ...]
    truth: TruthSpec
    source_path: Path

    @property
    def day_start(self) -> datetime:
        return self.timeline[0].start

    @property
    def day_end(self) -> datetime:
        return self.timeline[-1].end


def load_scenario(path: Path) -> Scenario:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: scenario must be a mapping")
    meta = _mapping(raw, "meta", path)
    name = _str(meta, "name")
    date = _str(meta, "date")
    seed = int(meta.get("seed", 0))
    display_id = int(meta.get("display_id", 1))
    width = int(meta.get("width", 1280))
    height = int(meta.get("height", 800))
    tzinfo = _parse_offset(str(meta.get("tz_offset", "+00:00")))

    cadence_raw = raw.get("cadence") or {}
    if not isinstance(cadence_raw, dict):
        raise ValueError(f"{path}: cadence must be a mapping")
    cadence = Cadence(
        default_seconds=int(cadence_raw.get("default_seconds", 45)),
        stretch_seconds=int(cadence_raw.get("stretch_seconds", 120)),
        tighten_seconds=int(cadence_raw.get("tighten_seconds", 30)),
        stretch_after_stable=int(cadence_raw.get("stretch_after_stable", 3)),
    )

    windows_raw = _mapping(raw, "windows", path)
    windows = {key: _window(key, value, path) for key, value in windows_raw.items()}
    if not windows:
        raise ValueError(f"{path}: windows must not be empty")

    blocks_raw = raw.get("timeline")
    if not isinstance(blocks_raw, list) or not blocks_raw:
        raise ValueError(f"{path}: timeline must be a non-empty list")
    timeline = tuple(_block(item, windows, date, tzinfo, path) for item in blocks_raw)
    _assert_timeline_order(timeline, path)

    truth = _truth(_mapping(raw, "truth", path), path)
    return Scenario(
        name=name,
        date=date,
        seed=seed,
        display_id=display_id,
        width=width,
        height=height,
        tzinfo=tzinfo,
        cadence=cadence,
        windows=windows,
        timeline=timeline,
        truth=truth,
        source_path=path,
    )


def content_corpus(content: dict[str, Any]) -> str:
    parts: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for inner in value.values():
                walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)

    walk(content)
    return "\n".join(parts)


def _window(key: str, raw: object, path: Path) -> WindowSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: window {key!r} must be a mapping")
    template = _str(raw, "template")
    if template not in TEMPLATES:
        raise ValueError(f"{path}: window {key!r} template {template!r} is unknown")
    url = raw.get("url")
    if url is not None:
        url = str(url)
    return WindowSpec(
        key=key,
        template=template,
        bundle_id=_str(raw, "bundle_id"),
        app_name=_str(raw, "app_name"),
        window_title=_str(raw, "window_title"),
        window_id=int(raw["window_id"]),
        url=url,
        is_private_browsing=bool(raw.get("is_private_browsing", False)),
        excluded=bool(raw.get("excluded", False)),
    )


def _block(
    raw: object,
    windows: dict[str, WindowSpec],
    date: str,
    tzinfo: timezone,
    path: Path,
) -> TimelineBlock:
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: timeline entry must be a mapping")
    window_key = _str(raw, "window")
    if window_key not in windows:
        raise ValueError(f"{path}: unknown window {window_key!r}")
    start = _parse_clock(date, _str(raw, "start"), tzinfo)
    end = _parse_clock(date, _str(raw, "end"), tzinfo)
    if end <= start:
        raise ValueError(f"{path}: block {window_key} end must be after start")
    states_raw = raw.get("states")
    if not isinstance(states_raw, list) or not states_raw:
        raise ValueError(f"{path}: block {window_key} needs at least one state")
    states = tuple(_state(item, date, tzinfo, path) for item in states_raw)
    if states[0].at > start:
        raise ValueError(f"{path}: first state of {window_key} must be at or before start")
    return TimelineBlock(window=windows[window_key], start=start, end=end, states=states)


def _state(raw: object, date: str, tzinfo: timezone, path: Path) -> ContentState:
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: state must be a mapping")
    content = raw.get("content")
    if not isinstance(content, dict):
        raise ValueError(f"{path}: state content must be a mapping")
    return ContentState(at=_parse_clock(date, _str(raw, "at"), tzinfo), content=content)


def _truth(raw: dict[str, Any], path: Path) -> TruthSpec:
    decisions = raw.get("decisions") or []
    commitments = raw.get("commitments") or []
    if not isinstance(decisions, list) or not isinstance(commitments, list):
        raise ValueError(f"{path}: decisions and commitments must be lists")
    return TruthSpec(
        projects=tuple(_str_list(raw, "projects")),
        people=tuple(_str_list(raw, "people")),
        decisions=tuple(item for item in decisions if isinstance(item, dict)),
        commitments=tuple(item for item in commitments if isinstance(item, dict)),
        completed=tuple(_str_list(raw, "completed")),
        not_completed=tuple(_str_list(raw, "not_completed")),
        noise=tuple(_str_list(raw, "noise")),
    )


def _assert_timeline_order(timeline: tuple[TimelineBlock, ...], path: Path) -> None:
    previous_end = timeline[0].start
    for block in timeline:
        if block.start < previous_end:
            raise ValueError(f"{path}: overlapping or out-of-order block {block.window.key}")
        previous_end = block.end


def _parse_clock(date: str, clock: str, tzinfo: timezone) -> datetime:
    return datetime.fromisoformat(f"{date}T{clock}").replace(tzinfo=tzinfo)


def _parse_offset(value: str) -> timezone:
    if value in {"Z", "+00:00", "-00:00"}:
        return UTC
    sign = 1 if value[0] == "+" else -1
    hours, minutes = value[1:].split(":")
    delta = timedelta(hours=int(hours), minutes=int(minutes))
    return timezone(sign * delta)


def _mapping(raw: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: {key} must be a mapping")
    return value


def _str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing string field {key!r}")
    return value


def _str_list(raw: dict[str, Any], key: str) -> list[str]:
    value = raw.get(key) or []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings")
    return value

"""YAML dialogue / edge-case script format for meeting fixtures."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

KINDS = frozenset({"dialogue", "silence", "tone", "one_second", "corrupt", "unsupported"})
TRAP_KINDS = frozenset({"suggestion-not-decision", "retracted-deadline", "absent-person-mentioned"})
SAMPLE_RATE = 16000
SAY_WORDS_PER_MIN = 175.0


@dataclass(frozen=True)
class Speaker:
    name: str
    voice: str
    role: str = ""


@dataclass(frozen=True)
class Line:
    speaker: str
    text: str
    voice: str | None = None
    pause_ms: int | None = None
    stage: str | None = None


@dataclass(frozen=True)
class NoiseSpec:
    kind: str = "white"
    amplitude: float = 0.008
    seed: int = 42


@dataclass(frozen=True)
class MeetingTruth:
    participants: tuple[str, ...] = ()
    decisions: tuple[dict[str, Any], ...] = ()
    commitments: tuple[dict[str, Any], ...] = ()
    project_updates: tuple[dict[str, Any], ...] = ()
    discussion_points: tuple[dict[str, Any], ...] = ()
    traps: tuple[dict[str, Any], ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class MeetingScript:
    kind: str
    name: str
    source_path: Path
    title: str = ""
    date: str = ""
    language: str = "en"
    sample_rate: int = SAMPLE_RATE
    target_duration_s: float | None = None
    duration_s: float | None = None
    frequency_hz: float = 440.0
    default_pause_ms: int = 700
    default_voice: str = "Samantha"
    container: str = ".xyz"
    speakers: dict[str, Speaker] = field(default_factory=dict)
    lines: tuple[Line, ...] = ()
    noise: NoiseSpec | None = None
    truth: MeetingTruth = field(default_factory=MeetingTruth)

    @property
    def is_dialogue(self) -> bool:
        return self.kind == "dialogue"


def load_meeting_script(path: Path) -> MeetingScript:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: meeting script must be a mapping")
    kind = str(raw.get("kind") or "dialogue")
    if kind not in KINDS:
        raise ValueError(f"{path}: kind must be one of {sorted(KINDS)}, got {kind!r}")
    meta = raw.get("meta") or {}
    if not isinstance(meta, dict):
        raise ValueError(f"{path}: meta must be a mapping")
    name = str(meta.get("name") or path.stem)
    if not name:
        raise ValueError(f"{path}: meta.name is required")

    speakers = _speakers(raw.get("speakers") or {}, path)
    lines = _lines(raw.get("lines") or [], speakers, path, kind)
    if kind == "dialogue" and not lines:
        raise ValueError(f"{path}: dialogue scripts need a non-empty lines list")
    for line in lines:
        if line.speaker not in speakers:
            raise ValueError(f"{path}: unknown speaker {line.speaker!r}")

    mix = raw.get("mix") or {}
    if mix and not isinstance(mix, dict):
        raise ValueError(f"{path}: mix must be a mapping")
    noise = _noise((mix or {}).get("noise") if isinstance(mix, dict) else None, path)

    duration = meta.get("duration_s")
    target = meta.get("target_duration_s")
    return MeetingScript(
        kind=kind,
        name=name,
        source_path=path,
        title=str(meta.get("title") or ""),
        date=str(meta.get("date") or ""),
        language=str(meta.get("language") or "en"),
        sample_rate=int(meta.get("sample_rate") or SAMPLE_RATE),
        target_duration_s=float(target) if target is not None else None,
        duration_s=float(duration) if duration is not None else _default_duration(kind),
        frequency_hz=float(meta.get("frequency_hz") or 440.0),
        default_pause_ms=_pause_ms(mix if isinstance(mix, dict) else None, meta),
        default_voice=str(meta.get("default_voice") or "Samantha"),
        container=str(meta.get("container") or ".xyz"),
        speakers=speakers,
        lines=lines,
        noise=noise,
        truth=_truth(raw.get("truth") or {}, path),
    )


def estimate_line_duration_s(text: str, words_per_min: float = SAY_WORDS_PER_MIN) -> float:
    words = [part for part in text.split() if part]
    if not words:
        return 0.0
    return max(0.35, len(words) * 60.0 / words_per_min)


def spoken_text(script: MeetingScript) -> str:
    return " ".join(line.text.strip() for line in script.lines if line.text.strip())


def _pause_ms(mix: dict[str, Any] | None, meta: dict[str, Any]) -> int:
    if mix is not None and mix.get("pause_ms") is not None:
        return int(mix["pause_ms"])
    if meta.get("default_pause_ms") is not None:
        return int(meta["default_pause_ms"])
    return 700


def _default_duration(kind: str) -> float | None:
    if kind == "one_second":
        return 1.0
    if kind in {"silence", "tone"}:
        return 15.0
    return None


def _speakers(raw: object, path: Path) -> dict[str, Speaker]:
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: speakers must be a mapping")
    out: dict[str, Speaker] = {}
    for name, value in raw.items():
        speaker_name = str(name)
        if isinstance(value, str):
            out[speaker_name] = Speaker(name=speaker_name, voice=value)
            continue
        if not isinstance(value, dict):
            raise ValueError(f"{path}: speaker {speaker_name!r} must be a voice or mapping")
        voice = str(value.get("voice") or "")
        if not voice:
            raise ValueError(f"{path}: speaker {speaker_name!r} needs a voice")
        out[speaker_name] = Speaker(
            name=speaker_name,
            voice=voice,
            role=str(value.get("role") or ""),
        )
    return out


def _lines(raw: object, speakers: dict[str, Speaker], path: Path, kind: str) -> tuple[Line, ...]:
    if raw in (None, []):
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{path}: lines must be a list")
    if kind != "dialogue":
        raise ValueError(f"{path}: lines are only valid on dialogue scripts")
    out: list[Line] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: lines[{index}] must be a mapping")
        speaker = str(item.get("speaker") or "")
        if not speaker:
            raise ValueError(f"{path}: lines[{index}] needs a speaker")
        text = str(item.get("line") or item.get("text") or "")
        stage = item.get("stage")
        if not text and not stage:
            raise ValueError(f"{path}: lines[{index}] needs a line or stage direction")
        pause = item.get("pause_ms")
        voice = item.get("voice")
        if voice is None and speaker in speakers:
            voice = speakers[speaker].voice
        out.append(
            Line(
                speaker=speaker,
                text=text,
                voice=str(voice) if voice else None,
                pause_ms=int(pause) if pause is not None else None,
                stage=str(stage) if stage else None,
            )
        )
    return tuple(out)


def _noise(raw: object, path: Path) -> NoiseSpec | None:
    if raw in (None, False):
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: mix.noise must be a mapping")
    kind = str(raw.get("kind") or "white")
    if kind != "white":
        raise ValueError(f"{path}: only mix.noise.kind=white is supported")
    return NoiseSpec(
        kind=kind,
        amplitude=float(raw.get("amplitude") or 0.008),
        seed=int(raw.get("seed") or 42),
    )


def _truth(raw: object, path: Path) -> MeetingTruth:
    if raw in (None, {}):
        return MeetingTruth()
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: truth must be a mapping")
    traps = raw.get("traps") or []
    if not isinstance(traps, list):
        raise ValueError(f"{path}: truth.traps must be a list")
    for trap in traps:
        if not isinstance(trap, dict):
            raise ValueError(f"{path}: each trap must be a mapping")
        kind = str(trap.get("kind") or "")
        if kind not in TRAP_KINDS:
            raise ValueError(f"{path}: trap kind {kind!r} is unknown")
    return MeetingTruth(
        participants=tuple(_str_list(raw, "participants", path)),
        decisions=tuple(_dict_list(raw, "decisions", path)),
        commitments=tuple(_dict_list(raw, "commitments", path)),
        project_updates=tuple(_dict_list(raw, "project_updates", path)),
        discussion_points=tuple(_dict_list(raw, "discussion_points", path)),
        traps=tuple(item for item in traps if isinstance(item, dict)),
        notes=str(raw.get("notes") or ""),
    )


def _str_list(raw: dict[str, Any], key: str, path: Path) -> list[str]:
    value = raw.get(key) or []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{path}: truth.{key} must be a list of strings")
    return value


def _dict_list(raw: dict[str, Any], key: str, path: Path) -> list[dict[str, Any]]:
    value = raw.get(key) or []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{path}: truth.{key} must be a list of mappings")
    return value

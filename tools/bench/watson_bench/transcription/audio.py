"""Load 16 kHz mono PCM from fixture WAVs."""

from __future__ import annotations

import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2


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


@dataclass(frozen=True)
class MeetingFixture:
    name: str
    path: Path
    wav_path: Path | None
    m4a_path: Path | None
    transcript_path: Path | None
    truth_path: Path | None
    kind: str
    pcm: bytes | None
    sample_rate: int
    duration_s: float
    reference_text: str
    load_error: str | None = None
    synthetic: bool = False

    @property
    def is_hallucination_probe(self) -> bool:
        return self.kind in {"silence", "tone"} or self.name in {
            "silence_only",
            "tone_only",
        }

    @property
    def is_decode_probe(self) -> bool:
        return self.kind in {"corrupt", "unsupported"}

    @property
    def exclude_from_wer(self) -> bool:
        """Linux tone placeholders must not pull aggregate WER off a real clip."""
        return self.synthetic or self.is_hallucination_probe


def load_pcm16_wav(path: Path) -> tuple[bytes, int, float]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.getnframes()
        pcm = handle.readframes(frames)
    if channels != 1:
        raise ValueError(f"{path}: expected mono, got {channels} channels")
    if width != SAMPLE_WIDTH:
        raise ValueError(f"{path}: expected 16-bit samples, got {width * 8}-bit")
    duration = frames / float(rate) if rate else 0.0
    return pcm, rate, duration


def is_near_silence(pcm: bytes, threshold: int = 80) -> bool:
    if not pcm:
        return True
    samples = array("h")
    samples.frombytes(pcm)
    return max(abs(int(sample)) for sample in samples) < threshold


def discover_meeting_fixtures(root: Path) -> list[MeetingFixture]:
    if not root.is_dir():
        raise FileNotFoundError(f"meeting fixtures directory not found: {root}")
    found: list[MeetingFixture] = []
    children = sorted(path for path in root.iterdir() if path.is_dir())
    targets = children or [root]
    for directory in targets:
        fixture = _load_one(directory)
        if fixture is not None:
            found.append(fixture)
    if not found:
        raise FileNotFoundError(f"no meeting fixtures under {root}")
    return found


def _load_one(directory: Path) -> MeetingFixture | None:
    wav = directory / "audio.wav"
    m4a = directory / "audio.m4a"
    extras = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.name.startswith("audio.") and path.suffix not in {".wav", ".m4a"}
    )
    transcript = directory / "transcript.txt"
    truth = directory / "truth.yaml"
    if not wav.is_file() and not m4a.is_file() and not extras and not transcript.is_file():
        return None
    kind, synthetic = _truth_fields(truth)
    pcm: bytes | None = None
    rate = SAMPLE_RATE
    duration = 0.0
    error: str | None = None
    if wav.is_file():
        try:
            pcm, rate, duration = load_pcm16_wav(wav)
        except (OSError, ValueError, wave.Error) as exc:
            error = str(exc)
    reference = ""
    if transcript.is_file():
        reference = spoken_from_transcript(transcript.read_text(encoding="utf-8"))
    return MeetingFixture(
        name=directory.name,
        path=directory,
        wav_path=wav if wav.is_file() else None,
        m4a_path=m4a if m4a.is_file() else None,
        transcript_path=transcript if transcript.is_file() else None,
        truth_path=truth if truth.is_file() else None,
        kind=kind or directory.name,
        pcm=pcm,
        sample_rate=rate,
        duration_s=duration,
        reference_text=reference,
        load_error=error,
        synthetic=synthetic,
    )


def _truth_fields(path: Path) -> tuple[str | None, bool]:
    if not path.is_file():
        return None, False
    try:
        import yaml
    except ImportError:
        return None, False
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return None, False
    kind = str(raw["kind"]) if raw.get("kind") else None
    return kind, bool(raw.get("synthetic"))

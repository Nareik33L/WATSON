"""TranscriptionRuntime protocol matching V1.1 plan A5.

Production copy lands at A1 in ``intelligence/runtime/transcription.py``.
This harness-only module is the A0 bake-off contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from watson_bench.adapters.base import Skipped


@dataclass(frozen=True)
class TranscriptSegment:
    start_ms: int
    end_ms: int
    text: str
    avg_logprob: float
    no_speech_prob: float
    compression_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TranscriptionRuntime(Protocol):
    name: str

    def availability(self, size: str, models_dir: Path) -> Skipped | None:
        """Return a skip reason, or None if this engine/size can run."""

    def load(self, size: str, models_dir: Path) -> None: ...

    def transcribe(
        self, pcm16k: bytes, language_hint: str | None
    ) -> list[TranscriptSegment]: ...

    def unload(self) -> None: ...


TRANSCRIBE_ENGINES = ("pywhispercpp", "mlx-whisper", "faster-whisper", "fake")
TRANSCRIBE_SIZES = ("tiny", "base", "small", "medium")
DEFAULT_ENGINES = ("pywhispercpp", "mlx-whisper", "faster-whisper")
DEFAULT_SIZES = ("base", "small")


def get_transcription_runtime(name: str) -> TranscriptionRuntime:
    from watson_bench.transcription.adapters import (
        FakeTranscriptionRuntime,
        FasterWhisperRuntime,
        MlxWhisperRuntime,
        PywhispercppRuntime,
    )

    mapping: dict[str, type[TranscriptionRuntime]] = {
        "fake": FakeTranscriptionRuntime,
        "faster-whisper": FasterWhisperRuntime,
        "mlx-whisper": MlxWhisperRuntime,
        "pywhispercpp": PywhispercppRuntime,
    }
    try:
        return mapping[name]()
    except KeyError as exc:
        raise ValueError(
            f"unknown transcription engine {name!r}; "
            f"choose from {', '.join(TRANSCRIBE_ENGINES)}"
        ) from exc

"""Harness transcription engines. Missing extras are skipped, not a crash."""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

from watson_bench.adapters.base import Skipped
from watson_bench.transcription.audio import is_near_silence
from watson_bench.transcription.protocol import TranscriptSegment
from watson_bench.transcription.wer import compression_ratio

FETCH_HINT = (
    "Download with WATSON_ALLOW_NETWORK=1 uv run watson-bench transcribe --fetch-only "
    "--profile 8gb --engines {engine} --sizes {size}"
)


class FakeTranscriptionRuntime:
    """Deterministic in-process engine for Linux CI and harness tests."""

    name = "fake"

    def __init__(self) -> None:
        self._loaded: str | None = None
        self.echo_text: str = ""

    def availability(self, size: str, models_dir: Path) -> Skipped | None:
        del size, models_dir
        return None

    def load(self, size: str, models_dir: Path) -> None:
        del models_dir
        self._loaded = size

    def transcribe(self, pcm16k: bytes, language_hint: str | None) -> list[TranscriptSegment]:
        del language_hint
        self._require()
        if is_near_silence(pcm16k):
            return []
        text = self.echo_text.strip()
        if not text:
            return []
        return [
            TranscriptSegment(
                start_ms=0,
                end_ms=max(1, int(len(pcm16k) / 32)),
                text=text,
                avg_logprob=-0.2,
                no_speech_prob=0.01,
                compression_ratio=compression_ratio(text),
            )
        ]

    def unload(self) -> None:
        self._loaded = None
        self.echo_text = ""

    def _require(self) -> None:
        if self._loaded is None:
            raise RuntimeError("fake transcription runtime has not loaded a model")


class FasterWhisperRuntime:
    name = "faster-whisper"

    def __init__(self) -> None:
        self._model: object | None = None
        self._size: str | None = None

    def availability(self, size: str, models_dir: Path) -> Skipped | None:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return Skipped(
                "faster-whisper is not installed. On the Mac: "
                "uv sync --group bench --extra transcribe"
            )
        if not _model_ready(models_dir / "faster-whisper" / size):
            return Skipped(FETCH_HINT.format(engine=self.name, size=size))
        return None

    def load(self, size: str, models_dir: Path) -> None:
        from faster_whisper import WhisperModel

        root = models_dir / "faster-whisper"
        self._model = WhisperModel(
            size,
            device="cpu",
            compute_type="int8",
            download_root=str(root),
            local_files_only=True,
        )
        self._size = size

    def transcribe(self, pcm16k: bytes, language_hint: str | None) -> list[TranscriptSegment]:
        if self._model is None:
            raise RuntimeError("faster-whisper model is not loaded")
        try:
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("numpy is required for faster-whisper") from exc
        audio = np.frombuffer(pcm16k, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = self._model.transcribe(  # type: ignore[attr-defined]
            audio,
            language=language_hint or "en",
            vad_filter=False,
        )
        out: list[TranscriptSegment] = []
        for segment in segments:
            text = str(getattr(segment, "text", "") or "").strip()
            out.append(
                TranscriptSegment(
                    start_ms=int(round(float(getattr(segment, "start", 0.0)) * 1000)),
                    end_ms=int(round(float(getattr(segment, "end", 0.0)) * 1000)),
                    text=text,
                    avg_logprob=float(getattr(segment, "avg_logprob", 0.0) or 0.0),
                    no_speech_prob=float(getattr(segment, "no_speech_prob", 0.0) or 0.0),
                    compression_ratio=float(
                        getattr(segment, "compression_ratio", None) or compression_ratio(text)
                    ),
                )
            )
        return out

    def unload(self) -> None:
        self._model = None
        self._size = None
        gc.collect()


class MlxWhisperRuntime:
    name = "mlx-whisper"

    def __init__(self) -> None:
        self._repo: str | None = None
        self._loaded = False

    def availability(self, size: str, models_dir: Path) -> Skipped | None:
        try:
            import mlx_whisper  # noqa: F401
        except ImportError:
            return Skipped(
                "mlx-whisper is not installed. On the Mac: "
                "uv sync --group bench --extra mlx-whisper"
            )
        dest = models_dir / "mlx-whisper" / size
        if not _model_ready(dest):
            return Skipped(FETCH_HINT.format(engine=self.name, size=size))
        return None

    def load(self, size: str, models_dir: Path) -> None:
        dest = models_dir / "mlx-whisper" / size
        if not dest.is_dir():
            raise RuntimeError(f"mlx-whisper weights missing at {dest}")
        self._repo = str(dest)
        self._loaded = True

    def transcribe(self, pcm16k: bytes, language_hint: str | None) -> list[TranscriptSegment]:
        if not self._loaded or self._repo is None:
            raise RuntimeError("mlx-whisper model is not loaded")
        import mlx_whisper

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as handle:
            _write_temp_wav(handle.name, pcm16k)
            result = mlx_whisper.transcribe(
                handle.name,
                path_or_hf_repo=self._repo,
                language=language_hint or "en",
            )
        segments = result.get("segments") if isinstance(result, dict) else None
        out: list[TranscriptSegment] = []
        for segment in segments or []:
            text = str(segment.get("text") or "").strip()
            out.append(
                TranscriptSegment(
                    start_ms=int(round(float(segment.get("start") or 0.0) * 1000)),
                    end_ms=int(round(float(segment.get("end") or 0.0) * 1000)),
                    text=text,
                    avg_logprob=float(segment.get("avg_logprob") or 0.0),
                    no_speech_prob=float(segment.get("no_speech_prob") or 0.0),
                    compression_ratio=float(
                        segment.get("compression_ratio") or compression_ratio(text)
                    ),
                )
            )
        return out

    def unload(self) -> None:
        self._repo = None
        self._loaded = False
        gc.collect()


class PywhispercppRuntime:
    name = "pywhispercpp"

    def __init__(self) -> None:
        self._model: object | None = None

    def availability(self, size: str, models_dir: Path) -> Skipped | None:
        try:
            import pywhispercpp.model  # noqa: F401
        except ImportError:
            return Skipped(
                "pywhispercpp is not installed. On the Mac: "
                "uv sync --group bench --extra pywhispercpp"
            )
        if not _model_ready(models_dir / "pywhispercpp" / size):
            return Skipped(FETCH_HINT.format(engine=self.name, size=size))
        return None

    def load(self, size: str, models_dir: Path) -> None:
        from pywhispercpp.model import Model

        dest = models_dir / "pywhispercpp" / size
        model_file = _first_gguf(dest)
        if model_file is None:
            raise RuntimeError(f"pywhispercpp weights missing at {dest}")
        self._model = Model(str(model_file), print_realtime=False, print_progress=False)

    def transcribe(self, pcm16k: bytes, language_hint: str | None) -> list[TranscriptSegment]:
        if self._model is None:
            raise RuntimeError("pywhispercpp model is not loaded")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as handle:
            _write_temp_wav(handle.name, pcm16k)
            kwargs: dict[str, object] = {}
            if language_hint:
                kwargs["language"] = language_hint
            segments = self._model.transcribe(handle.name, **kwargs)  # type: ignore[attr-defined]
        out: list[TranscriptSegment] = []
        for segment in segments or []:
            text = str(getattr(segment, "text", "") or "").strip()
            start_s = _cpp_seconds(segment, "t0", "start")
            end_s = _cpp_seconds(segment, "t1", "end")
            out.append(
                TranscriptSegment(
                    start_ms=int(round(start_s * 1000)),
                    end_ms=int(round(end_s * 1000)),
                    text=text,
                    avg_logprob=float(getattr(segment, "p", 0.0) or 0.0),
                    no_speech_prob=0.0,
                    compression_ratio=compression_ratio(text),
                )
            )
        return out

    def unload(self) -> None:
        self._model = None
        gc.collect()


def _model_ready(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_file() and path.stat().st_size > 0:
        return True
    if not path.is_dir():
        return False
    return any(child.is_file() and child.stat().st_size > 0 for child in path.rglob("*"))


def _first_gguf(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    bins = sorted(directory.rglob("*.bin")) + sorted(directory.rglob("*.gguf"))
    return bins[0] if bins else None


def _cpp_seconds(segment: object, centi_name: str, sec_name: str) -> float:
    if hasattr(segment, sec_name):
        try:
            return float(getattr(segment, sec_name))
        except (TypeError, ValueError):
            pass
    raw = getattr(segment, centi_name, 0) or 0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    # whisper.cpp exposes t0/t1 in centiseconds.
    return value / 100.0 if value > 20 else value


def _write_temp_wav(path: str, pcm16k: bytes, sample_rate: int = 16000) -> None:
    import wave

    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm16k)

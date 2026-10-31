"""WER (jiwer) and hallucination scoring for A0."""

from __future__ import annotations

import re
import zlib
from collections.abc import Sequence

from watson_bench.transcription.protocol import TranscriptSegment

_PUNCT = re.compile(r"[^\w\s']+", re.UNICODE)


def join_segment_text(segments: Sequence[TranscriptSegment]) -> str:
    return " ".join(segment.text.strip() for segment in segments if segment.text.strip())


def word_error_rate(reference: str, hypothesis: str) -> float | None:
    ref = _normalize(reference)
    hyp = _normalize(hypothesis)
    if not ref and not hyp:
        return 0.0
    if not ref:
        return None
    try:
        import jiwer
    except ImportError as exc:
        raise RuntimeError(
            "jiwer is not installed. From the repo root: uv sync --group bench"
        ) from exc
    return float(jiwer.wer(ref, hyp))


def hallucination(segments: Sequence[TranscriptSegment]) -> dict[str, object]:
    """Any non-empty text on a silence/tone probe counts as a hallucination.

    The raw text is not stored: bench output must not keep transcript content.
    """
    nonempty = [segment.text.strip() for segment in segments if segment.text.strip()]
    return {
        "hallucinated": bool(nonempty),
        "n_nonempty_segments": len(nonempty),
        "n_words": len(" ".join(nonempty).split()),
    }


def compression_ratio(text: str) -> float:
    raw = text.encode("utf-8")
    if not raw:
        return 0.0
    compressed = zlib.compress(raw)
    return len(raw) / max(len(compressed), 1)


def _normalize(text: str) -> str:
    stripped = _PUNCT.sub(" ", text.lower())
    return " ".join(stripped.split())

"""macOS A0 smoke: faster-whisper tiny on a `say`-rendered known-text clip."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from watson_bench.paths import default_models_dir
from watson_bench.transcription.models import model_ready
from watson_bench.transcription.run import run_transcription
from watson_fixturegen.meetings.render import render_meeting
from watson_fixturegen.paths import meetings_scripts_dir

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "meetings"
WER_CEILING = 0.50


@pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS say + faster-whisper tiny")
def test_faster_whisper_tiny_speech_wer_rtf_not_skipped(tmp_path: Path) -> None:
    models_dir = default_models_dir()
    if not model_ready(models_dir, "faster-whisper", "tiny"):
        pytest.fail(
            "faster-whisper tiny is not on disk at "
            f"{models_dir / 'faster-whisper' / 'tiny'}. "
            "Fetch must succeed before this smoke (WATSON_ALLOW_NETWORK=1 "
            "uv run watson-bench transcribe --fetch-only --engines faster-whisper --sizes tiny)."
        )
    render_meeting(
        meetings_scripts_dir() / "speech_short.yaml",
        FIXTURES / "speech_short",
    )
    out = run_transcription(
        profile="8gb",
        engines=["faster-whisper"],
        sizes=["tiny"],
        fixtures_dir=FIXTURES,
        out_dir=tmp_path / "smoke",
        models_dir=models_dir,
    )
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert manifest["results"], manifest
    row = manifest["results"][0]
    assert row["skipped"] is False, row.get("skip_reason")
    assert row["error"] is None, row.get("error")
    assert row["realtime_factor"] is not None and row["realtime_factor"] > 0
    speech = next(item for item in row["fixtures"] if item["name"] == "speech_short")
    assert speech.get("skipped") is not True
    assert speech["wer"] is not None
    assert speech["wer"] <= WER_CEILING, speech

from __future__ import annotations

import wave
from pathlib import Path

from watson_fixturegen.secrets import scan_texts

REPO = Path(__file__).resolve().parents[2]
MEETINGS = REPO / "tests" / "fixtures" / "meetings"
SCRIPTS = REPO / "tools" / "fixture_gen" / "meetings"
# Plan: commit compressed audio + transcripts only if the total is ≤ 8 MB.
MAX_COMMITTED_BYTES = 8 * 1024 * 1024


def _file_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def test_committed_edge_fixtures_exist() -> None:
    for name in ("silence_only", "tone_only", "one_second"):
        wav = MEETINGS / name / "audio.wav"
        assert wav.is_file(), name
        with wave.open(str(wav), "rb") as handle:
            assert handle.getnchannels() == 1
            assert handle.getsampwidth() == 2
            assert handle.getframerate() == 16000
        transcript = (MEETINGS / name / "transcript.txt").read_text(encoding="utf-8")
        assert transcript.strip() == ""
    corrupt = MEETINGS / "corrupt" / "audio.wav"
    assert corrupt.is_file()
    assert corrupt.read_bytes().startswith(b"RIFF")
    assert (MEETINGS / "unsupported" / "audio.xyz").is_file()


def test_standup_audio_is_artifacts_only() -> None:
    standup_wav = MEETINGS / "standup_12m" / "audio.wav"
    standup_m4a = MEETINGS / "standup_12m" / "audio.m4a"
    assert not standup_wav.is_file(), "do not commit the 16 kHz stand-up WAV"
    assert not standup_m4a.is_file(), (
        "stand-up audio is a macos-15 artifact until a Mac render is ≤ 8 MB"
    )
    assert (SCRIPTS / "standup_12m.yaml").is_file()


def test_committed_meeting_audio_under_8mb() -> None:
    total = _file_bytes(MEETINGS)
    assert total <= MAX_COMMITTED_BYTES, f"committed meetings fixtures are {total} bytes"


def test_meeting_texts_have_no_real_brands() -> None:
    texts = [path.read_text(encoding="utf-8") for path in SCRIPTS.glob("*.yaml")]
    texts.extend(path.read_text(encoding="utf-8") for path in MEETINGS.rglob("truth.yaml"))
    texts.extend(path.read_text(encoding="utf-8") for path in MEETINGS.rglob("transcript.txt"))
    assert scan_texts(texts) == []

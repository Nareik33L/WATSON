from __future__ import annotations

import sys
import wave
from pathlib import Path

import pytest

from watson_fixturegen.cli import main
from watson_fixturegen.meetings.macos import LINUX_DIALOGUE_ERROR, MacOSRequiredError
from watson_fixturegen.meetings.render import render_meeting
from watson_fixturegen.meetings.script import load_meeting_script
from watson_fixturegen.paths import meetings_scripts_dir

SCRIPTS = meetings_scripts_dir()


def test_linux_dialogue_render_fails_clearly(tmp_path: Path) -> None:
    if sys.platform == "darwin":
        pytest.skip("dialogue render is allowed on macOS")
    with pytest.raises(MacOSRequiredError, match="macOS"):
        render_meeting(SCRIPTS / "standup_12m.yaml", tmp_path / "out")
    assert "say" in LINUX_DIALOGUE_ERROR


def test_dry_run_standup_writes_transcript_and_truth(tmp_path: Path) -> None:
    out = tmp_path / "standup_12m"
    result = render_meeting(SCRIPTS / "standup_12m.yaml", out, dry_run=True)
    assert result.dry_run
    assert result.wav_path is None
    assert result.duration_ms >= 720_000
    transcript = result.transcript_path.read_text(encoding="utf-8")
    assert "Nora Voss:" in transcript
    assert "[00:00.000-" in transcript
    truth = result.truth_path.read_text(encoding="utf-8")
    assert "Helen Cho" in truth
    assert "Wednesday" in truth


def test_edge_scripts_render_on_linux(tmp_path: Path) -> None:
    silence = render_meeting(SCRIPTS / "silence.yaml", tmp_path / "silence_only")
    assert silence.wav_path is not None and silence.wav_path.is_file()
    with wave.open(str(silence.wav_path), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16000
        assert abs(handle.getnframes() / 16000 - 15) < 0.05
    assert silence.transcript_path.read_text(encoding="utf-8") == ""

    tone = render_meeting(SCRIPTS / "tone.yaml", tmp_path / "tone_only")
    assert tone.wav_path is not None
    assert tone.wav_path.stat().st_size > 1000

    one = render_meeting(SCRIPTS / "one_second.yaml", tmp_path / "one_second")
    assert one.duration_ms == 1000

    corrupt = render_meeting(SCRIPTS / "corrupt.yaml", tmp_path / "corrupt")
    assert corrupt.wav_path is not None
    assert corrupt.wav_path.read_bytes().startswith(b"RIFF")
    with pytest.raises(wave.Error):
        wave.open(str(corrupt.wav_path), "rb")

    unsupported = render_meeting(SCRIPTS / "unsupported.yaml", tmp_path / "unsupported")
    xyz = unsupported.out_dir / "audio.xyz"
    assert xyz.is_file()
    assert b"not a media" in xyz.read_bytes()


def test_cli_meetings_render_edge(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "silence_only"
    assert main(["meetings", "render", str(SCRIPTS / "silence.yaml"), "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert "wav:" in captured.out
    assert (out / "audio.wav").is_file()


def test_cli_meetings_dialogue_fails_on_linux(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if sys.platform == "darwin":
        pytest.skip("dialogue render is allowed on macOS")
    code = main(
        [
            "meetings",
            "render",
            str(SCRIPTS / "standup_12m.yaml"),
            "--out",
            str(tmp_path / "nope"),
        ]
    )
    assert code == 1
    assert "macOS" in capsys.readouterr().err


def test_load_edge_kinds() -> None:
    assert load_meeting_script(SCRIPTS / "silence.yaml").kind == "silence"
    assert load_meeting_script(SCRIPTS / "tone.yaml").frequency_hz == 440.0
    assert load_meeting_script(SCRIPTS / "unsupported.yaml").container == ".xyz"

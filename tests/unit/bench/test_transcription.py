from __future__ import annotations

import json
from pathlib import Path

import pytest

from watson_bench.cli import main
from watson_bench.hardware import HardwareInfo
from watson_bench.metrics import RssSampler, current_rss_bytes
from watson_bench.network import NetworkGuardError, guard_is_active
from watson_bench.transcription.adapters import FakeTranscriptionRuntime
from watson_bench.transcription.audio import load_pcm16_wav, spoken_from_transcript
from watson_bench.transcription.protocol import TranscriptSegment, get_transcription_runtime
from watson_bench.transcription.report import render_transcription_report
from watson_bench.transcription.run import run_transcription
from watson_bench.transcription.wer import compression_ratio, hallucination, word_error_rate
from watson_fixturegen.meetings.render import render_meeting
from watson_fixturegen.paths import meetings_scripts_dir

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "tests" / "fixtures" / "meetings"


def _pcm_from_committed(name: str) -> bytes:
    pcm, rate, _duration = load_pcm16_wav(FIXTURES / name / "audio.wav")
    assert rate == 16000
    return pcm


def test_fake_runtime_silence_is_empty() -> None:
    runtime = FakeTranscriptionRuntime()
    assert runtime.availability("base", Path("/tmp")) is None
    runtime.load("base", Path("/tmp"))
    segments = runtime.transcribe(_pcm_from_committed("silence_only"), "en")
    assert segments == []
    runtime.unload()
    with pytest.raises(RuntimeError, match="not loaded"):
        runtime.transcribe(_pcm_from_committed("silence_only"), "en")


def test_fake_runtime_echoes_reference_on_speech() -> None:
    runtime = get_transcription_runtime("fake")
    runtime.load("small", Path("/tmp"))
    runtime.echo_text = "good morning everyone"  # type: ignore[attr-defined]
    # Tone file has energy; fake echoes the attached reference when present.
    segments = runtime.transcribe(_pcm_from_committed("tone_only"), "en")
    assert segments
    assert segments[0].text == "good morning everyone"
    runtime.echo_text = ""  # type: ignore[attr-defined]
    assert runtime.transcribe(_pcm_from_committed("tone_only"), "en") == []
    assert segments[0].start_ms == 0
    assert "avg_logprob" in segments[0].to_dict()
    runtime.unload()


def test_wer_and_hallucination_plumbing() -> None:
    assert word_error_rate("good morning everyone", "good morning everyone") == 0.0
    assert word_error_rate("", "") == 0.0
    wer = word_error_rate("good morning everyone", "good evening everyone")
    assert wer is not None and wer > 0
    empty = hallucination([])
    assert empty["hallucinated"] is False
    hit = hallucination(
        [TranscriptSegment(0, 1000, "thanks for watching", -0.1, 0.2, 1.0)]
    )
    assert hit["hallucinated"] is True
    assert hit["n_nonempty_segments"] == 1
    assert compression_ratio("aaaa" * 40) > compression_ratio("unique words here")


def test_rss_sampler_records_a_peak_during_fake_transcribe() -> None:
    before = current_rss_bytes()
    sampler = RssSampler(interval_s=0.05)
    sampler.start()
    runtime = FakeTranscriptionRuntime()
    runtime.load("base", Path("/tmp"))
    runtime.transcribe(_pcm_from_committed("one_second"), "en")
    peak = sampler.stop()
    runtime.unload()
    assert peak >= before


def test_spoken_from_transcript_matches_fixturegen() -> None:
    raw = "[01:02.500-01:05.000] Samir Qureshi: Export lands Wednesday.\n"
    assert spoken_from_transcript(raw) == "Export lands Wednesday."


def test_run_transcription_fake_writes_json_and_report(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    out = run_transcription(
        profile="8gb",
        engines=["fake"],
        sizes=["base"],
        fixtures_dir=FIXTURES,
        out_dir=tmp_path / "run",
        report_path=report,
    )
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "transcription"
    assert manifest["results"]
    row = manifest["results"][0]
    assert row["engine"] == "fake"
    assert row["size"] == "base"
    assert row["skipped"] is False
    assert row["load_time_s"] is not None
    assert row["peak_rss_bytes"]
    assert row["hallucination"]["silence_only"]["hallucinated"] is False
    assert row["hallucination"]["tone_only"]["hallucinated"] is False
    text = report.read_text(encoding="utf-8")
    assert "A0 transcription bake-off" in text
    assert "Recommendation" in text
    assert "_tbd_" in text


def test_cli_transcribe_fake(tmp_path: Path) -> None:
    report = tmp_path / "cli.md"
    code = main(
        [
            "transcribe",
            "--profile",
            "8gb",
            "--engines",
            "fake",
            "--sizes",
            "base",
            "--fixtures",
            str(FIXTURES),
            "--out",
            str(tmp_path / "cli-run"),
            "--report",
            str(report),
        ]
    )
    assert code == 0
    assert report.is_file()
    assert (tmp_path / "cli-run" / "run.json").is_file()


def test_missing_engine_skips(tmp_path: Path) -> None:
    out = run_transcription(
        profile="8gb",
        engines=["faster-whisper"],
        sizes=["tiny"],
        fixtures_dir=FIXTURES,
        out_dir=tmp_path / "skip",
        models_dir=tmp_path / "no-models",
    )
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    row = manifest["results"][0]
    assert row["skipped"] is True
    assert row["skip_reason"]
    assert "fetch" in row["skip_reason"] or "not installed" in row["skip_reason"]


def test_fetch_only_requires_allow_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("WATSON_ALLOW_NETWORK", raising=False)
    code = main(
        [
            "transcribe",
            "--profile",
            "8gb",
            "--engines",
            "faster-whisper",
            "--sizes",
            "tiny",
            "--fetch-only",
            "--models-dir",
            str(tmp_path / "models"),
        ]
    )
    assert code == 1
    assert "WATSON_ALLOW_NETWORK" in capsys.readouterr().err


def test_report_table_includes_wer_and_rtf() -> None:
    text = render_transcription_report(
        hardware=HardwareInfo(
            chip="Apple M1",
            ram_gb=8.0,
            os_name="macOS",
            os_version="14.5",
            arch="arm64",
            python_version="3.12.3",
        ),
        profile="8gb",
        results=[
            {
                "engine": "fake",
                "size": "base",
                "skipped": False,
                "load_time_s": 0.01,
                "realtime_factor": 0.05,
                "wer": 0.12,
                "peak_rss_bytes": 40 * 1024**2,
                "audio_s": 15.0,
                "wall_s": 0.7,
                "hallucination": {
                    "silence_only": {"hallucinated": False, "n_nonempty_segments": 0},
                    "tone_only": {"hallucinated": True, "n_nonempty_segments": 1},
                },
            }
        ],
        fixtures_dir="tests/fixtures/meetings",
        fixture_count=3,
    )
    assert "12.0%" in text
    assert "FAIL" in text
    assert "empty" in text


def test_transcribe_run_enables_the_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    from watson_bench.transcription import run as run_mod

    seen = {"active": False}
    original = run_mod._run_one

    def wrapped(*args: object, **kwargs: object):
        seen["active"] = guard_is_active()
        with pytest.raises(NetworkGuardError):
            socket.getaddrinfo("example.com", 443)
        return original(*args, **kwargs)

    monkeypatch.setattr(run_mod, "_run_one", wrapped)
    run_transcription(
        profile="8gb",
        engines=["fake"],
        sizes=["tiny"],
        fixtures_dir=FIXTURES,
        out_dir=tmp_path / "guard",
    )
    assert seen["active"]
    assert not guard_is_active()


def test_models_unload_between_sizes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from watson_bench.transcription import run as run_mod
    from watson_bench.transcription.adapters import FakeTranscriptionRuntime

    events: list[str] = []
    original_get = run_mod.get_transcription_runtime

    class TrackingFake(FakeTranscriptionRuntime):
        def load(self, size: str, models_dir: Path) -> None:
            events.append(f"load:{size}")
            super().load(size, models_dir)

        def unload(self) -> None:
            events.append("unload")
            super().unload()

    monkeypatch.setattr(run_mod, "get_transcription_runtime", lambda name: TrackingFake())
    del original_get
    run_transcription(
        profile="8gb",
        engines=["fake"],
        sizes=["tiny", "base"],
        fixtures_dir=FIXTURES,
        out_dir=tmp_path / "unload",
    )
    assert events == ["load:tiny", "unload", "load:base", "unload"]


def test_edge_render_roundtrip_for_harness(tmp_path: Path) -> None:
    dest = tmp_path / "silence_only"
    render_meeting(meetings_scripts_dir() / "silence.yaml", dest)
    out = run_transcription(
        profile="8gb",
        engines=["fake"],
        sizes=["tiny"],
        fixtures_dir=tmp_path,
        out_dir=tmp_path / "run",
    )
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert manifest["results"][0]["hallucination"]["silence_only"]["hallucinated"] is False

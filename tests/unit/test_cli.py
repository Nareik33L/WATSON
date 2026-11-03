"""CLI smoke tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from watson import __version__, cli
from watson.memory.keys import InMemoryKeyProvider, set_key_provider

TEST_KEY = bytes.fromhex("11" * 32)


def test_cli_imports_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert callable(cli.main)

    with pytest.raises(SystemExit) as exited:
        cli.main(["--version"])

    assert exited.value.code == 0
    out = capsys.readouterr().out
    assert f"W.A.T.S.O.N {__version__}" in out
    with pytest.raises(SystemExit) as help_exit:
        cli.main(["--help"])
    assert help_exit.value.code == 0
    help_out = capsys.readouterr().out
    assert "W.A.T.S.O.N — local-first work memory." in help_out
    assert "usage: watson" in help_out


def test_eval_help_is_not_a_stub(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main(["eval", "--help"])
    assert exited.value.code == 0
    out = capsys.readouterr().out
    assert "--mode" in out
    assert "replay" in out
    assert "--runtime" in out
    assert "--llm-model" in out
    assert "not implemented" not in out


def _eval_key(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = InMemoryKeyProvider(TEST_KEY)
    set_key_provider(provider)
    monkeypatch.setattr("watson.memory.keys.get_key_provider", lambda: provider)


def test_eval_cli_replays_and_writes_report(
    data_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from watson.intelligence.eval.run import DayEval, EvalReport

    _eval_key(monkeypatch)
    captured: dict[str, object] = {}

    def fake_run_eval(**kwargs: object) -> EvalReport:
        captured.update(kwargs)
        out_dir = Path(str(kwargs["out_dir"]))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text("{}\n", encoding="utf-8")
        (out_dir / "report.md").write_text("# ok\n", encoding="utf-8")
        return EvalReport(
            mode="replay",
            runtime="replay",
            days=[
                DayEval(
                    name="day_acme_q4",
                    date="2026-10-31",
                    metrics={"project_f1": 1.0},
                    derived_first={"summaries": 1},
                    derived_second={"summaries": 1},
                    summary_present=True,
                    identical_counts=True,
                    narrative_nonempty=True,
                    citation_count=1,
                    claims=1,
                )
            ],
            aggregate={
                "project_f1": 1.0,
                "people_f1": 1.0,
                "completed_accuracy": 1.0,
                "decisions_recall": 1.0,
                "decisions_precision": 1.0,
                "commitments_recall": 1.0,
                "commitments_precision": 1.0,
                "unsupported_achievements": 0.0,
                "duplicate_observation_rate": 0.0,
                "noise_non_work_rate": 1.0,
                "claims_with_evidence": 1.0,
                "citations_resolvable": 1.0,
            },
            gate={"passed": True, "failures": [], "hard_failures": []},
            wall_seconds=0.01,
            peak_rss_bytes=1,
        )

    monkeypatch.setattr("watson.intelligence.eval.run.run_eval", fake_run_eval)
    assert cli.main(["eval", "--mode", "replay", "--out", str(tmp_path / "eval")]) == 0
    out = capsys.readouterr().out
    assert "eval_mode: replay" in out
    assert "eval_pipeline_passed: True" in out
    assert captured["mode"] == "replay"
    assert captured["runtime_name"] == "replay"
    set_key_provider(None)


def test_eval_cli_llm_model_overrides_config(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from watson.config.schema import FALLBACK_LLM_MODEL
    from watson.intelligence.eval.run import DayEval, EvalReport

    _eval_key(monkeypatch)
    captured: dict[str, object] = {}

    def fake_run_eval(**kwargs: object) -> EvalReport:
        captured.update(kwargs)
        out_dir = Path(str(kwargs["out_dir"]))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text("{}\n", encoding="utf-8")
        (out_dir / "report.md").write_text("# ok\n", encoding="utf-8")
        return EvalReport(
            mode="replay",
            runtime="replay",
            days=[
                DayEval(
                    name="day_acme_q4",
                    date="2026-10-31",
                    metrics={"project_f1": 1.0},
                    derived_first={"summaries": 1},
                    derived_second={"summaries": 1},
                    summary_present=True,
                    identical_counts=True,
                    narrative_nonempty=True,
                    citation_count=1,
                    claims=1,
                )
            ],
            aggregate={
                "project_f1": 1.0,
                "people_f1": 1.0,
                "completed_accuracy": 1.0,
                "decisions_recall": 1.0,
                "decisions_precision": 1.0,
                "commitments_recall": 1.0,
                "commitments_precision": 1.0,
                "unsupported_achievements": 0.0,
                "duplicate_observation_rate": 0.0,
                "noise_non_work_rate": 1.0,
                "claims_with_evidence": 1.0,
                "citations_resolvable": 1.0,
            },
            gate={"passed": True, "failures": [], "hard_failures": []},
        )

    monkeypatch.setattr("watson.intelligence.eval.run.run_eval", fake_run_eval)
    assert (
        cli.main(
            [
                "eval",
                "--mode",
                "replay",
                "--llm-model",
                FALLBACK_LLM_MODEL,
                "--out",
                str(tmp_path / "eval"),
            ]
        )
        == 0
    )
    config = captured["config"]
    assert getattr(config.intelligence, "llm_model") == FALLBACK_LLM_MODEL
    assert getattr(config.intelligence.ollama, "timeout_s") == 300.0
    set_key_provider(None)


@pytest.mark.parametrize("model", ["qwen2.5:7b", "llama3.1:8b", "gemma2:9b"])
def test_eval_cli_llm_model_applies_large_preset(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    model: str,
) -> None:
    from watson.config.schema import LARGE_LLM_TIMEOUT_S
    from watson.intelligence.eval.run import DayEval, EvalReport

    _eval_key(monkeypatch)
    captured: dict[str, object] = {}

    def fake_run_eval(**kwargs: object) -> EvalReport:
        captured.update(kwargs)
        out_dir = Path(str(kwargs["out_dir"]))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text("{}\n", encoding="utf-8")
        (out_dir / "report.md").write_text("# ok\n", encoding="utf-8")
        return EvalReport(
            mode="replay",
            runtime="replay",
            days=[
                DayEval(
                    name="day_acme_q4",
                    date="2026-10-31",
                    metrics={"project_f1": 1.0},
                    derived_first={"summaries": 1},
                    derived_second={"summaries": 1},
                    summary_present=True,
                    identical_counts=True,
                    narrative_nonempty=True,
                    citation_count=1,
                    claims=1,
                )
            ],
            aggregate={
                "project_f1": 1.0,
                "people_f1": 1.0,
                "completed_accuracy": 1.0,
                "decisions_precision": 1.0,
                "decisions_recall": 1.0,
                "commitments_precision": 1.0,
                "commitments_recall": 1.0,
                "unsupported_achievements": 0.0,
                "duplicate_observation_rate": 0.0,
                "noise_non_work_rate": 1.0,
                "claims_with_evidence": 1.0,
                "citations_resolvable": 1.0,
            },
            gate={"passed": True, "failures": [], "hard_failures": []},
        )

    monkeypatch.setattr("watson.intelligence.eval.run.run_eval", fake_run_eval)
    assert (
        cli.main(
            ["eval", "--mode", "replay", "--llm-model", model, "--out", str(tmp_path / "eval")]
        )
        == 0
    )
    config = captured["config"]
    assert getattr(config.intelligence, "llm_model") == model
    assert getattr(config.intelligence.ollama, "timeout_s") == 3600.0
    assert getattr(config.intelligence.ollama, "timeout_s") == LARGE_LLM_TIMEOUT_S
    assert config.intelligence.ollama_num_ctx() == 4096
    set_key_provider(None)


def test_eval_cli_exits_1_when_gate_fails(
    data_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from watson.intelligence.eval.run import EvalReport

    _eval_key(monkeypatch)

    def failing(**kwargs: object) -> EvalReport:
        return EvalReport(
            mode="replay",
            runtime="replay",
            days=[],
            aggregate={},
            gate={"passed": False, "failures": ["empty_aggregate"], "hard_failures": []},
        )

    monkeypatch.setattr("watson.intelligence.eval.run.run_eval", failing)
    assert cli.main(["eval", "--mode", "replay", "--out", str(tmp_path / "eval")]) == 1
    set_key_provider(None)


def test_eval_cli_exits_1_on_zero_fixtures(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from watson.intelligence.eval.run import EvalReport
    from watson.intelligence.eval.run import run_eval as real_run

    _eval_key(monkeypatch)

    def zero_fixtures(**kwargs: object) -> EvalReport:
        kwargs = dict(kwargs)
        kwargs["names"] = ()
        return real_run(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("watson.intelligence.eval.run.run_eval", zero_fixtures)
    assert cli.main(["eval", "--mode", "replay", "--out", str(tmp_path / "empty")]) == 1
    set_key_provider(None)


def test_eval_cli_exits_1_on_hard_failure_without_strict(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from watson.intelligence.eval.run import DayEval, EvalReport

    _eval_key(monkeypatch)
    metrics = {
        "project_f1": 1.0,
        "people_f1": 1.0,
        "completed_accuracy": 1.0,
        "decisions_recall": 1.0,
        "decisions_precision": 1.0,
        "commitments_recall": 1.0,
        "commitments_precision": 1.0,
        "unsupported_achievements": 1.0,
        "duplicate_observation_rate": 0.0,
        "noise_non_work_rate": 1.0,
        "claims_with_evidence": 1.0,
        "citations_resolvable": 1.0,
    }

    def hard_fail(**kwargs: object) -> EvalReport:
        return EvalReport(
            mode="replay",
            runtime="replay",
            days=[
                DayEval(
                    name="day_acme_q4",
                    date="2026-10-31",
                    metrics=metrics,
                    derived_first={"summaries": 1},
                    derived_second={"summaries": 1},
                    summary_present=True,
                    identical_counts=True,
                    narrative_nonempty=True,
                    citation_count=1,
                    claims=1,
                )
            ],
            aggregate=metrics,
            gate={
                "passed": False,
                "failures": ["unsupported_achievements"],
                "hard_failures": ["unsupported_achievements"],
            },
        )

    monkeypatch.setattr("watson.intelligence.eval.run.run_eval", hard_fail)
    assert cli.main(["eval", "--mode", "replay", "--out", str(tmp_path / "hard")]) == 1
    set_key_provider(None)


def test_eval_cli_strict_exits_1_on_threshold_miss(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from watson.intelligence.eval.run import DayEval, EvalReport

    _eval_key(monkeypatch)

    def soft_miss(**kwargs: object) -> EvalReport:
        return EvalReport(
            mode="replay",
            runtime="replay",
            days=[
                DayEval(
                    name="day_acme_q4",
                    date="2026-10-31",
                    metrics={"project_f1": 0.1},
                    derived_first={"summaries": 1},
                    derived_second={"summaries": 1},
                    summary_present=True,
                    identical_counts=True,
                    narrative_nonempty=True,
                    citation_count=1,
                    claims=1,
                )
            ],
            aggregate={
                "project_f1": 0.1,
                "people_f1": 1.0,
                "completed_accuracy": 1.0,
                "decisions_recall": 1.0,
                "decisions_precision": 1.0,
                "commitments_recall": 1.0,
                "commitments_precision": 1.0,
                "unsupported_achievements": 0.0,
                "duplicate_observation_rate": 0.0,
                "noise_non_work_rate": 1.0,
                "claims_with_evidence": 1.0,
                "citations_resolvable": 1.0,
            },
            gate={"passed": False, "failures": ["project_f1"], "hard_failures": []},
        )

    monkeypatch.setattr("watson.intelligence.eval.run.run_eval", soft_miss)
    assert cli.main(["eval", "--mode", "replay", "--out", str(tmp_path / "s")]) == 0
    assert cli.main(["eval", "--mode", "replay", "--strict", "--out", str(tmp_path / "s")]) == 1
    set_key_provider(None)


@pytest.mark.parametrize("value,code", [(0.84, 1), (0.85, 0)])
def test_eval_cli_strict_min_spec_gates_precision(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value: float,
    code: int,
) -> None:
    """--strict --profile min-spec must still fail at precision 0.84 and pass at 0.85."""
    from watson.intelligence.eval.run import DayEval, EvalReport
    from watson.intelligence.eval.thresholds import gate_metrics, gate_profile, load_thresholds

    _eval_key(monkeypatch)
    metrics = {name: 1.0 for name in load_thresholds()}
    metrics["unsupported_achievements"] = 0.0
    metrics["duplicate_observation_rate"] = 0.0
    metrics["decisions_precision"] = value

    def fake_run_eval(**kwargs: object) -> EvalReport:
        assert kwargs.get("profile") == "min-spec"
        out_dir = Path(str(kwargs["out_dir"]))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text("{}\n", encoding="utf-8")
        (out_dir / "report.md").write_text("# ok\n", encoding="utf-8")
        return EvalReport(
            mode="replay",
            runtime="replay",
            days=[
                DayEval(
                    name="day_acme_q4",
                    date="2026-10-31",
                    metrics=metrics,
                    derived_first={"summaries": 1},
                    derived_second={"summaries": 1},
                    summary_present=True,
                    identical_counts=True,
                    narrative_nonempty=True,
                    citation_count=1,
                    claims=1,
                )
            ],
            aggregate=metrics,
            gate=gate_metrics(metrics),
            profile="min-spec",
            profile_gate=gate_profile(
                understand_p95_s=120.0,
                worker_plus_model_peak_bytes=8 * 1024**3,
                consolidate_s=10_000.0,
                completed_without_kill=1.0,
                profile="min-spec",
            ),
        )

    monkeypatch.setattr("watson.intelligence.eval.run.run_eval", fake_run_eval)
    assert (
        cli.main(
            [
                "eval",
                "--mode",
                "replay",
                "--strict",
                "--profile",
                "min-spec",
                "--out",
                str(tmp_path / f"strict-min-spec-{value}"),
            ]
        )
        == code
    )
    set_key_provider(None)


@pytest.mark.parametrize("command", ["run", "models"])
def test_stub_commands_not_implemented(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([command]) == 1
    assert "not implemented" in capsys.readouterr().err


def test_doctor_and_db_commands(
    data_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = InMemoryKeyProvider(TEST_KEY)
    set_key_provider(provider)
    monkeypatch.setattr("watson.memory.keys.get_key_provider", lambda: provider)

    assert cli.main(["doctor"]) == 0
    doctor = capsys.readouterr().out
    assert doctor.startswith("W.A.T.S.O.N doctor")
    assert str(data_dir) in doctor
    assert "db_status: missing" in doctor
    assert TEST_KEY.hex() not in doctor
    assert "config_path:" in doctor
    assert "spool_dir:" in doctor

    assert cli.main(["db", "upgrade"]) == 0
    upgraded = capsys.readouterr().out
    assert "0010_m4b_corrections" in upgraded
    assert TEST_KEY.hex() not in upgraded

    assert cli.main(["db", "status"]) == 0
    status = capsys.readouterr().out
    assert "db_status: ok" in status
    assert "db_journal_mode: wal" in status
    assert "db_fts5: available" in status
    assert "db_sqlite_vec:" in status
    assert "alembic_status: head" in status
    assert TEST_KEY.hex() not in status

    assert cli.main(["doctor"]) == 0
    after = capsys.readouterr().out
    assert "db_status: ok" in after
    assert "key_provider: in-memory" in after
    set_key_provider(None)


def test_status_without_db(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "db_status: missing" in out
    assert "capture.state: idle" in out
    assert "queue.depth: 0" in out
    assert "backlog.depth: 0" in out
    assert "frames.captured: 0" in out
    assert "deletion.verified: 0" in out
    assert "exclusions.override: none" in out
    assert "last_replay: none" in out
    assert "last_frames: none" in out
    assert "audit: none" in out


def test_db_without_subcommand_is_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["db"]) == 1
    assert "upgrade" in capsys.readouterr().err


def test_bad_config_exits_2(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (data_dir / "config.toml").write_text(
        '[capture]\ninterval_seconds = "nope"\n',
        encoding="utf-8",
    )
    assert cli.main(["doctor"]) == 2
    err = capsys.readouterr().err
    assert "capture.interval_seconds" in err


def test_eval_cli_exits_1_on_temperature_mismatch(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Config temperature 0.7 vs a file recorded at 0.0 must exit non-zero."""
    import json

    from watson.intelligence.eval.recordings import RecordingRuntime

    _eval_key(monkeypatch)
    recordings = tmp_path / "named.json"

    class _NamedInner:
        name = "ollama"
        llm_model = "qwen2.5:7b"
        temperature = 0.0

        def complete(
            self,
            messages: list[dict[str, str]],
            schema: dict[str, object],
            *,
            num_predict: int | None = None,
        ) -> dict[str, object]:
            del messages, schema, num_predict
            return {"commitments": [{"text": "named"}]}

    runtime = RecordingRuntime(path=recordings, inner=_NamedInner(), mode="record")
    runtime.complete(
        [
            {
                "role": "user",
                "content": (
                    "# WATSON day extract — v1\n\n"
                    "From the day's numbered claims and known entities, extract:\n"
                    "Claims:\n1. Alice\n"
                ),
            }
        ],
        {},
    )
    runtime.save()
    payload = json.loads(recordings.read_text(encoding="utf-8"))
    assert payload["temperature"] == 0.0

    real_load = cli.load_config

    def load_hot() -> object:
        cfg = real_load()
        intel = cfg.intelligence.model_copy(update={"temperature": 0.7, "llm_model": "qwen2.5:7b"})
        return cfg.model_copy(update={"intelligence": intel})

    monkeypatch.setattr(cli, "load_config", load_hot)
    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    assert (
        cli.main(
            [
                "eval",
                "--mode",
                "replay",
                "--recordings",
                str(recordings),
                "--fixtures",
                str(fixtures),
                "--only",
                "day_near_empty",
                "--out",
                str(tmp_path / "eval"),
            ]
        )
        == 1
    )
    set_key_provider(None)

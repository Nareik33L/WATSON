from __future__ import annotations

import json
from pathlib import Path

from watson_bench.cli import main
from watson_bench.paths import samples_dir
from watson_bench.run import run_bakeoff


def test_cli_hardware_and_list(capsys) -> None:
    assert main(["hardware"]) == 0
    out = capsys.readouterr().out
    assert "RAM" in out

    assert main(["list", "--profile", "8gb", "--runtime", "ollama"]) == 0
    listed = capsys.readouterr().out
    assert "ollama-qwen25vl-3b" in listed
    assert "CANDIDATES TO BE CONFIRMED" in listed


def test_cli_help() -> None:
    try:
        main(["--help"])
    except SystemExit as exited:
        assert exited.code == 0


def test_fake_runtime_end_to_end(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    out = run_bakeoff(
        profile="8gb",
        frames_dir=samples_dir(),
        out_dir=tmp_path / "run",
        runtimes=["fake"],
        report_path=report,
    )
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert manifest["frame_count"] == 5
    assert manifest["hardware"]["os_name"]
    assert manifest["results"]
    assert all(not row["skipped"] for row in manifest["results"])
    assert any(row["candidate"]["role"] == "vlm" for row in manifest["results"])
    assert any(row["candidate"]["role"] == "llm" for row in manifest["results"])
    assert any(row["candidate"]["role"] == "embeddings" for row in manifest["results"])
    for row in manifest["results"]:
        assert row["peak_rss_bytes"]
        assert row["load_time_s"] is not None
        if row["candidate"]["role"] == "vlm":
            assert row["time_per_frame_s"]
        if row["candidate"]["role"] == "llm":
            assert row["time_per_call_s"]
        if row["candidate"]["role"] == "embeddings":
            assert row["embedding_texts_per_s"]
    assert (out / "scoring.csv").is_file()
    csv_text = (out / "scoring.csv").read_text(encoding="utf-8")
    assert "visible_ok" in csv_text
    assert "hallucination" in csv_text
    frame_jsons = list((out / "frames").glob("*.json"))
    assert frame_jsons
    report_text = report.read_text(encoding="utf-8")
    assert "Recommendation" in report_text
    assert "tokens/s" in report_text


def test_cli_fake_run_writes_report(tmp_path: Path) -> None:
    report = tmp_path / "cli-report.md"
    code = main(
        [
            "run",
            "--profile",
            "8gb",
            "--runtime",
            "fake",
            "--frames",
            str(samples_dir()),
            "--out",
            str(tmp_path / "cli-run"),
            "--report",
            str(report),
        ]
    )
    assert code == 0
    assert report.is_file()
    assert "Recommendation" in report.read_text(encoding="utf-8")
    assert (tmp_path / "cli-run" / "run.json").is_file()


def test_run_does_not_fetch_missing_runtimes(tmp_path: Path) -> None:
    out = run_bakeoff(
        profile="8gb",
        frames_dir=samples_dir(),
        out_dir=tmp_path / "skip",
        models_dir=tmp_path / "no-models",
        runtimes=["mlx"],
    )
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert manifest["results"]
    assert all(row["skipped"] for row in manifest["results"])
    reasons = [row["skip_reason"] or "" for row in manifest["results"]]
    assert all("fetch" in reason or "not installed" in reason for reason in reasons)

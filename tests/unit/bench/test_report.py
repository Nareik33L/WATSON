from __future__ import annotations

from pathlib import Path

from watson_bench.hardware import HardwareInfo
from watson_bench.report import render_report, write_report


def _hardware() -> HardwareInfo:
    return HardwareInfo(
        chip="Apple M1",
        ram_gb=8.0,
        os_name="macOS",
        os_version="14.5",
        arch="arm64",
        python_version="3.12.3",
    )


def test_report_has_table_and_blank_recommendation(tmp_path: Path) -> None:
    results = [
        {
            "candidate": {
                "id": "ollama-qwen25vl-3b",
                "role": "vlm",
                "family": "Qwen2.5-VL",
                "runtime": "ollama",
                "quantisation": "q4_K_M",
            },
            "skipped": False,
            "tokens_per_s": 12.5,
            "time_per_frame_s": 4.2,
            "peak_rss_bytes": 2 * 1024**3,
            "load_time_s": 3.1,
        },
        {
            "candidate": {"id": "mlx-missing", "role": "llm", "runtime": "mlx"},
            "skipped": True,
            "skip_reason": "mlx-lm is not installed",
        },
    ]
    text = render_report(
        hardware=_hardware(),
        profile="8gb",
        results=results,
        frames_dir="tools/bench/samples/frames",
        frame_count=5,
        scoring_csv="/tmp/scoring.csv",
    )
    assert "Apple M1" in text
    assert "8gb" in text
    assert "`ollama-qwen25vl-3b`" in text
    assert "Recommendation" in text
    assert "_tbd_" in text
    assert "mlx-lm is not installed" in text
    path = write_report(
        tmp_path / "2026-10-31.md",
        hardware=_hardware(),
        profile="8gb",
        results=results,
        frames_dir="tools/bench/samples/frames",
        frame_count=5,
    )
    assert path.is_file()
    assert "Recommendation" in path.read_text(encoding="utf-8")

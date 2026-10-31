from __future__ import annotations

from pathlib import Path

from watson_bench.paths import bench_root, candidates_dir, repo_root, samples_dir


def test_bench_paths_resolve_in_checkout() -> None:
    root = bench_root()
    assert (root / "candidates" / "vlm.yaml").is_file()
    assert candidates_dir().is_dir()
    assert samples_dir().is_dir()
    assert (repo_root() / "pyproject.toml").is_file()
    assert (repo_root() / "docs" / "WATSON_V1_ENGINEERING_PLAN.md").is_file()


def test_env_override(tmp_path: Path, monkeypatch) -> None:
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    monkeypatch.setenv("WATSON_BENCH_ROOT", str(tmp_path))
    assert bench_root() == tmp_path

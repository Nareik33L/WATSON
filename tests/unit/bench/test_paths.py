from __future__ import annotations

from pathlib import Path

import pytest

from watson_bench.cli import main
from watson_bench.paths import (
    bench_root,
    candidates_dir,
    default_report_path,
    default_results_dir,
    repo_root,
    samples_dir,
    set_repo_root,
)


def _checkout() -> Path:
    return Path(__file__).resolve().parents[3]


def test_bench_paths_resolve_in_checkout() -> None:
    root = bench_root()
    assert (root / "candidates" / "vlm.yaml").is_file()
    assert candidates_dir().is_dir()
    assert samples_dir().is_dir()
    assert (repo_root() / "pyproject.toml").is_file()
    assert (repo_root() / "docs" / "WATSON_V1_ENGINEERING_PLAN.md").is_file()


def test_default_outputs_are_in_the_checkout() -> None:
    checkout = _checkout()
    assert repo_root() == checkout
    assert default_results_dir() == checkout / "tools" / "bench" / "results"
    assert default_report_path("2026-10-31") == (
        checkout / "docs" / "benchmarks" / "2026-10-31.md"
    )
    assert ".venv" not in default_results_dir().parts
    assert "site-packages" not in default_results_dir().parts


def test_install_tree_is_not_used_for_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages" / "watson_bench"
    bundled = site / "data" / "candidates"
    bundled.mkdir(parents=True)
    (bundled / "vlm.yaml").write_text("role: vlm\nprofiles: {}\n", encoding="utf-8")
    monkeypatch.setattr("watson_bench.paths.package_dir", lambda: site)
    checkout = _checkout()
    monkeypatch.chdir(checkout)
    assert default_results_dir() == checkout / "tools" / "bench" / "results"
    assert default_report_path("2026-10-31").parent == checkout / "docs" / "benchmarks"
    assert candidates_dir() == checkout / "tools" / "bench" / "candidates"
    assert (candidates_dir() / "vlm.yaml").is_file()
    yaml = (candidates_dir() / "vlm.yaml").read_text(encoding="utf-8")
    assert "profiles:" in yaml
    assert "role: vlm\nprofiles: {}" not in yaml


def test_env_repo_root_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='watson'\n", encoding="utf-8")
    (tmp_path / "tools" / "bench").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    monkeypatch.setenv("WATSON_REPO_ROOT", str(tmp_path))
    assert repo_root() == tmp_path.resolve()
    assert default_results_dir() == tmp_path.resolve() / "tools" / "bench" / "results"
    assert default_report_path("2026-10-31") == (
        tmp_path.resolve() / "docs" / "benchmarks" / "2026-10-31.md"
    )


def test_env_override_bench_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    monkeypatch.setenv("WATSON_BENCH_ROOT", str(tmp_path))
    assert bench_root() == tmp_path.resolve()


def test_cli_repo_root_flag(tmp_path: Path) -> None:
    assert main(["--repo-root", str(tmp_path), "hardware"]) == 0
    assert repo_root() == tmp_path.resolve()
    assert default_results_dir() == tmp_path.resolve() / "tools" / "bench" / "results"


def test_missing_repo_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WATSON_REPO_ROOT", raising=False)
    monkeypatch.delenv("WATSON_BENCH_REPO", raising=False)
    monkeypatch.delenv("WATSON_BENCH_ROOT", raising=False)
    site = tmp_path / ".venv" / "lib" / "site-packages" / "watson_bench"
    site.mkdir(parents=True)
    monkeypatch.setattr("watson_bench.paths.package_dir", lambda: site)
    monkeypatch.setattr("watson_bench.paths.git_toplevel", lambda _cwd: None)
    set_repo_root(None)
    with pytest.raises(FileNotFoundError, match="repo root"):
        repo_root()

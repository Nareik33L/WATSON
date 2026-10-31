from __future__ import annotations

import os
import subprocess
import sysconfig
from pathlib import Path


def _isolated_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = ""
    return env


def _cli() -> Path:
    script = Path(sysconfig.get_path("scripts")) / "watson-bench"
    assert script.is_file(), f"installed entry point missing at {script}"
    return script


def test_installed_watson_bench_help_without_pythonpath(tmp_path: Path) -> None:
    proc = subprocess.run(
        [str(_cli()), "--help"],
        cwd=tmp_path,
        env=_isolated_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "transcribe" in proc.stdout
    assert "hardware" in proc.stdout

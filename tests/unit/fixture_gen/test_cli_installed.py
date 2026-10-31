from __future__ import annotations

import os
import subprocess
import sys
import sysconfig
from pathlib import Path


def _isolated_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = ""
    return env


def _cli() -> Path:
    script = Path(sysconfig.get_path("scripts")) / "watson-fixturegen"
    assert script.is_file(), f"installed entry point missing at {script}"
    return script


def test_installed_cli_help_from_outside_repo(tmp_path: Path) -> None:
    proc = subprocess.run(
        [str(_cli()), "--help"],
        cwd=tmp_path,
        env=_isolated_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "render" in proc.stdout


def test_installed_package_ships_fonts_and_scenarios(tmp_path: Path) -> None:
    probe = """
from importlib.resources import files
root = files("watson_fixturegen")
for rel in (
    "fonts/Inter-Regular.ttf",
    "fonts/Inter-SemiBold.ttf",
    "fonts/OFL.txt",
    "scenarios/day_acme_q4.yaml",
    "templates/base.html",
):
    item = root.joinpath(*rel.split("/"))
    assert item.is_file(), rel
    assert len(item.read_bytes()) > 0, rel
print("ok")
"""
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=_isolated_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ok" in proc.stdout


def test_installed_cli_dry_run_bundled_scenario(tmp_path: Path) -> None:
    locate = subprocess.run(
        [
            sys.executable,
            "-c",
            "from watson_fixturegen.paths import scenarios_dir; "
            "print(scenarios_dir() / 'day_acme_q4.yaml')",
        ],
        cwd=tmp_path,
        env=_isolated_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert locate.returncode == 0, locate.stderr
    scenario = locate.stdout.strip()
    assert scenario.endswith("day_acme_q4.yaml")
    out = tmp_path / "out"
    proc = subprocess.run(
        [str(_cli()), "render", scenario, "--out", str(out), "--dry-run"],
        cwd=tmp_path,
        env=_isolated_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert (out / "truth.yaml").is_file()
    assert (out / "sample50.txt").is_file()

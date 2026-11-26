"""Prepare a local virtual environment before the application imports its dependencies.

This module uses the standard library only so it can run before those packages exist.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def ensure_runtime(root: Path) -> None:
    if sys.version_info < (3, 11):
        raise SystemExit(
            f"Python 3.11 or newer is required. This interpreter is {sys.version.split()[0]}."
        )
    venv_py = _venv_python(root)
    if Path(sys.executable).resolve() != venv_py.resolve():
        if not venv_py.exists():
            _say("Creating a local virtual environment at .venv")
            _create_venv(root)
            venv_py = _venv_python(root)
        _ensure_pip(venv_py)
        if not _has_deps(venv_py):
            _install(venv_py, root)
        os.environ["WATSON_BOOTSTRAPPED"] = "1"
        _say("Using the project virtual environment")
        os.execv(str(venv_py), [str(venv_py), *sys.argv])
    if _importable():
        return
    if os.environ.get("WATSON_DEPS_INSTALLED") == "1":
        raise SystemExit("Dependencies are still missing after install. See the pip output above.")
    _install(Path(sys.executable), root)
    os.environ["WATSON_DEPS_INSTALLED"] = "1"
    os.execv(sys.executable, [sys.executable, *sys.argv])


def _create_venv(root: Path) -> None:
    target = root / ".venv"
    if target.exists():
        shutil.rmtree(target)
    created = subprocess.run(
        [sys.executable, "-m", "venv", str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if created.returncode != 0:
        _say("Python has no ensurepip module. Creating the environment and installing pip into it.")
        if target.exists():
            shutil.rmtree(target)
        fallback = subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if fallback.returncode != 0:
            detail = (created.stderr or fallback.stderr or "").strip()
            raise SystemExit(f"Could not create .venv.\n{detail}")


def _ensure_pip(python: Path) -> None:
    if _pip_ready(python):
        return
    _say("Installing pip into the project virtual environment")
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "--python",
            str(python),
            "install",
            "--disable-pip-version-check",
            "pip",
        ]
    )


def _pip_ready(python: Path) -> bool:
    result = subprocess.run(
        [str(python), "-m", "pip", "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _install(python: Path, root: Path) -> None:
    _say("Installing Python dependencies from requirements.txt")
    subprocess.check_call(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(root / "requirements.txt")]
    )


def _has_deps(python: Path) -> bool:
    result = subprocess.run(
        [str(python), "-c", "import fastapi, uvicorn, sqlalchemy, yaml, pydantic"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _importable() -> bool:
    try:
        import fastapi  # noqa: F401
        import pydantic  # noqa: F401
        import sqlalchemy  # noqa: F401
        import uvicorn  # noqa: F401
        import yaml  # noqa: F401
    except ImportError:
        return False
    return True


def _venv_python(root: Path) -> Path:
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def _say(message: str) -> None:
    from datetime import datetime

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{stamp} INFO  watson.startup  {message}", flush=True)

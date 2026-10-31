"""OS-level no-network check (plan §6 / §11.3).

Linux CI also runs the full fixture replay under ``unshare -rn``. This
module covers the same namespace isolation with a tiny fixture so the
unit suite stays fast, and skips when unshare is unavailable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.replay_support import TEST_KEY, write_mini_fixture


def _unshare_available() -> bool:
    if shutil.which("unshare") is None:
        return False
    probe = subprocess.run(
        ["unshare", "-rn", "true"],
        check=False,
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


@pytest.mark.skipif(not _unshare_available(), reason="unshare -rn is not available")
def test_replay_and_db_status_under_unshare(tmp_path: Path) -> None:
    fixture = write_mini_fixture(tmp_path, frames=1)
    data_dir = tmp_path / "data"
    env = os.environ.copy()
    env["WATSON_DATA_DIR"] = str(data_dir)
    env["WATSON_DB_KEY"] = TEST_KEY.hex()
    env.pop("WATSON_ALLOW_NETWORK", None)
    watson = Path(sys.executable).resolve().parent / "watson"
    exe = str(watson) if watson.is_file() else sys.executable
    replay_cmd = (
        ["unshare", "-rn", "--", exe, "replay", str(fixture), "--instant"]
        if watson.is_file()
        else [
            "unshare",
            "-rn",
            "--",
            sys.executable,
            "-m",
            "watson.cli",
            "replay",
            str(fixture),
            "--instant",
        ]
    )
    status_cmd = (
        ["unshare", "-rn", "--", exe, "db", "status"]
        if watson.is_file()
        else [
            "unshare",
            "-rn",
            "--",
            sys.executable,
            "-m",
            "watson.cli",
            "db",
            "status",
        ]
    )
    replay = subprocess.run(replay_cmd, check=False, capture_output=True, text=True, env=env)
    assert replay.returncode == 0, replay.stderr
    assert "deletion_verified: 1" in replay.stdout
    status = subprocess.run(status_cmd, check=False, capture_output=True, text=True, env=env)
    assert status.returncode == 0, status.stderr
    assert "db_status: ok" in status.stdout

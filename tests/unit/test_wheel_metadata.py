"""Optional local re-run of the wheel METADATA check.

``uv build`` fetches hatchling from PyPI, so this must not run under the
netns no-network harness. The dedicated CI job is the real gate.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from watson.privacy.network import ALLOW_NETWORK_ENV

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(
    os.environ.get(ALLOW_NETWORK_ENV) != "1",
    reason=(
        "uv build fetches hatchling from PyPI; set WATSON_ALLOW_NETWORK=1 "
        "or use the wheel-metadata CI job"
    ),
)
def test_watson_wheel_metadata_does_not_require_watson_bench(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "tools" / "check_wheel_metadata.py"),
            "--out-dir",
            str(tmp_path),
        ],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "ok " in proc.stdout
    assert "watson-bench" not in proc.stdout

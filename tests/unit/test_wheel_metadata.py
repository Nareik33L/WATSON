"""The product wheel must not depend on watson-bench (unclaimed PyPI name)."""

from __future__ import annotations

import email
import subprocess
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _requires_dist(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        assert names, f"no METADATA in {wheel.name}"
        parsed = email.message_from_bytes(archive.read(names[0]))
    return parsed.get_all("Requires-Dist") or []


def test_watson_wheel_metadata_does_not_require_watson_bench(tmp_path: Path) -> None:
    proc = subprocess.run(
        ["uv", "build", "--wheel", "--package", "watson", "--out-dir", str(tmp_path)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    wheels = list(tmp_path.glob("watson-*.whl"))
    assert len(wheels) == 1, wheels
    requires = _requires_dist(wheels[0])
    bench = [item for item in requires if "watson-bench" in item]
    assert bench == [], f"product wheel must not require watson-bench: {requires}"

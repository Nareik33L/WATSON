#!/usr/bin/env python3
"""Assert the watson product wheel does not Require-Dist watson-bench.

Used by the dedicated CI job (outside netns). ``uv build`` may fetch
hatchling from PyPI — do not run this under the no-network harness.
"""

from __future__ import annotations

import argparse
import email
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def requires_dist(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if not names:
            raise SystemExit(f"no METADATA in {wheel.name}")
        parsed = email.message_from_bytes(archive.read(names[0]))
    return parsed.get_all("Requires-Dist") or []


def build_watson_wheel(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["uv", "build", "--wheel", "--package", "watson", "--out-dir", str(out_dir)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or proc.stdout)
        raise SystemExit(proc.returncode)
    wheels = list(out_dir.glob("watson-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected one watson wheel, got {wheels}")
    return wheels[0]


def assert_no_watson_bench(wheel: Path) -> None:
    requires = requires_dist(wheel)
    bench = [item for item in requires if "watson-bench" in item]
    if bench:
        raise SystemExit(f"product wheel must not require watson-bench: {requires}")
    print(f"ok {wheel.name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("/tmp/watson-wheel"))
    args = parser.parse_args(argv)
    wheel = build_watson_wheel(args.out_dir)
    assert_no_watson_bench(wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

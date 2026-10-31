from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
# Soft target is 10 MB per day; CI fails above 12 MB. No Git LFS.
MAX_DAY_BYTES = 12 * 1024 * 1024


def _day_bytes(day: Path) -> int:
    return sum(path.stat().st_size for path in day.rglob("*") if path.is_file())


def test_each_fixture_day_stays_under_size_budget() -> None:
    days = sorted(path for path in FIXTURES.iterdir() if path.is_dir())
    assert days, "expected at least one fixture day"
    for day in days:
        total = _day_bytes(day)
        assert total <= MAX_DAY_BYTES, f"{day.name} is {total} bytes; budget is {MAX_DAY_BYTES}"

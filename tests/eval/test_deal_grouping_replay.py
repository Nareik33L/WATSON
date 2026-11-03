"""Replay fixture coverage for v2 deal clustering and per-deal summaries."""

from __future__ import annotations

import json
from pathlib import Path

from tests.replay_support import TEST_KEY
from watson.intelligence.eval.run import run_eval

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
RECORDINGS = FIXTURES / "eval" / "recordings.json"


def test_recordings_emit_v2_deals_for_acme_day() -> None:
    payload = json.loads(RECORDINGS.read_text(encoding="utf-8"))
    rows = [row for row in payload["complete"] if isinstance(row, dict)]
    acme = next(
        row
        for row in rows
        if "Acme Q4 proposal" in str(row.get("match") or "")
        or "Acme Q4 proposal"
        in json.dumps((row.get("response") or {}).get("deals") or [])
    )
    deals = (acme.get("response") or {}).get("deals") or []
    assert deals
    assert any(item.get("title") == "Acme Q4 proposal" for item in deals)
    cluster = next(row for row in rows if str(row.get("match") or "").startswith("Cluster these"))
    assert (cluster.get("response") or {}).get("deals")


def test_replay_acme_day_produces_v2_deals(tmp_path: Path, key_provider: object) -> None:
    report = run_eval(
        fixtures_root=FIXTURES,
        names=("day_acme_q4",),
        mode="replay",
        runtime_name="replay",
        recordings=RECORDINGS,
        out_dir=tmp_path / "eval",
        key=TEST_KEY,
    )
    assert report.days
    day = report.days[0]
    assert day.derived_first.get("deals", 0) >= 1
    assert day.derived_first.get("summaries", 0) >= 1
    assert day.narrative_nonempty
    assert day.citation_count > 0
    assert day.metrics["citations_resolvable"] == 1.0

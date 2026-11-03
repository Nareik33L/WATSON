"""Replay fixture coverage for v2 deal clustering and per-deal summaries."""

from __future__ import annotations

import json
from pathlib import Path

from tests.replay_support import TEST_KEY
from watson.intelligence.eval.run import run_eval
from watson.memory.db import connect

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
RECORDINGS = FIXTURES / "eval" / "recordings.json"


def test_recordings_emit_v2_deals_for_acme_day() -> None:
    payload = json.loads(RECORDINGS.read_text(encoding="utf-8"))
    rows = [row for row in payload["complete"] if isinstance(row, dict)]
    acme = next(
        row
        for row in rows
        if str(row.get("match") or "").endswith("Acme Q4 proposal.")
        or "Acme Q4 proposal" in json.dumps((row.get("response") or {}).get("deals") or [])
    )
    deals = (acme.get("response") or {}).get("deals") or []
    assert deals
    assert any(item.get("title") == "Acme Q4 proposal" for item in deals)
    assert str(acme.get("match") or "").endswith(".")
    cluster = next(row for row in rows if str(row.get("match") or "").startswith("Cluster these"))
    assert (cluster.get("response") or {}).get("deals")
    assert (cluster.get("response") or {}).get("deals")[0]["title"] == "Day work"


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
    conn = connect(tmp_path / "eval" / "day_acme_q4" / "watson.db", TEST_KEY, load_vec=False)
    try:
        titles = [
            str(row[0]) for row in conn.execute("SELECT title FROM deals ORDER BY created_at, id")
        ]
        assert titles == ["Acme Q4 proposal"]
        n_none = int(
            conn.execute("SELECT COUNT(*) FROM segments WHERE deal_id IS NULL").fetchone()[0]
        )
        n_assigned = int(
            conn.execute("SELECT COUNT(*) FROM segments WHERE deal_id IS NOT NULL").fetchone()[0]
        )
        assert n_assigned == 8
        assert n_none == 5
        assert n_none + n_assigned == 13
    finally:
        conn.close()

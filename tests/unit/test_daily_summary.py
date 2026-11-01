"""Daily narrative in consolidation: citations, Unverified, idempotent replace."""

from __future__ import annotations

import json
from pathlib import Path

from tests.unit.test_consolidation import (
    NOW,
    _insert_claim,
    _insert_day,
    _open,
    _runtime,
    _seed_day,
)
from watson.intelligence.citations import citations_resolvable
from watson.intelligence.runtime import FakeRuntime
from watson.pipeline.consolidation import consolidate_working_day, derived_counts


def test_summary_cites_day_claims_and_lists_uncited(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        runtime = _runtime()
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        row = conn.execute(
            """
            SELECT narrative_md, sections_json, claim_ids, prompt_version, pipeline_version
            FROM daily_summaries WHERE working_day_id = ?
            """,
            (seeded["day_id"],),
        ).fetchone()
        assert row is not None
        assert row["prompt_version"] == "daily_summary/v1"
        assert int(row["pipeline_version"]) == 1
        narrative = str(row["narrative_md"])
        assert "[c:" in narrative
        assert narrative.count("[c:") == 2
        day_ids = {
            str(item[0])
            for item in conn.execute(
                "SELECT id FROM claims WHERE working_day_id = ? AND pipeline_version = 1",
                (seeded["day_id"],),
            )
        }
        assert citations_resolvable(narrative, day_ids)
        stored = set(json.loads(str(row["claim_ids"])))
        assert stored
        assert stored <= day_ids
        sections = json.loads(str(row["sections_json"]))
        unverified_ids = {item["id"] for item in sections["unverified"]}
        assert unverified_ids == day_ids - stored
        assert derived_counts(conn, seeded["day_id"])["summaries"] == 1
    finally:
        conn.close()


def test_summary_strips_foreign_and_missing_citations(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        other_day = _insert_day(conn, date="2026-10-30")
        other = _insert_claim(
            conn,
            conn.execute("SELECT id FROM observations LIMIT 1").fetchone()[0],
            other_day,
            seeded["changed"],
            conn.execute("SELECT id FROM frames LIMIT 1").fetchone()[0],
            kind="outcome",
            assertion="observed",
            text="belongs elsewhere",
        )
        missing = "99999999-9999-4999-8999-999999999999"
        runtime = FakeRuntime(
            complete_queue=[
                {"activities": []},
                {"entity_deltas": [], "decisions": [], "commitments": []},
                {
                    "paragraphs": [
                        {
                            "text": (
                                f"Keep the draft [c:{seeded['observed']}]. "
                                f"Drop the ghost [c:{missing}]. "
                                f"Drop the other day [c:{other}]."
                            ),
                            "claim_ids": [seeded["observed"], missing, other],
                        }
                    ]
                },
            ]
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        narrative = str(
            conn.execute(
                "SELECT narrative_md FROM daily_summaries WHERE working_day_id = ?",
                (seeded["day_id"],),
            ).fetchone()[0]
        )
        assert f"[c:{seeded['observed']}]" in narrative
        assert missing not in narrative
        assert other not in narrative
        day_ids = {
            str(item[0])
            for item in conn.execute(
                "SELECT id FROM claims WHERE working_day_id = ?", (seeded["day_id"],)
            )
        }
        assert citations_resolvable(narrative, day_ids)
    finally:
        conn.close()


def test_reconsolidation_keeps_identical_summary_counts(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        runtime = _runtime()
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        first = derived_counts(conn, seeded["day_id"])
        narrative = str(
            conn.execute(
                "SELECT narrative_md FROM daily_summaries WHERE working_day_id = ?",
                (seeded["day_id"],),
            ).fetchone()[0]
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        assert derived_counts(conn, seeded["day_id"]) == first
        assert conn.execute("SELECT COUNT(*) FROM daily_summaries").fetchone()[0] == 1
        again = str(
            conn.execute(
                "SELECT narrative_md FROM daily_summaries WHERE working_day_id = ?",
                (seeded["day_id"],),
            ).fetchone()[0]
        )
        assert again == narrative
    finally:
        conn.close()

"""Daily narrative in consolidation: citations, Unverified, idempotent replace."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.test_consolidation import (
    NOW,
    RECORDINGS,
    _insert_claim,
    _insert_day,
    _insert_observation,
    _open,
    _runtime,
    _seed_day,
)
from watson.intelligence.citations import citations_resolvable
from watson.intelligence.runtime import FakeRuntime, RuntimeUnavailable
from watson.pipeline.consolidation import (
    ConsolidateSettings,
    ConsolidationError,
    consolidate_working_day,
    derived_counts,
)
from watson.privacy.retention import Tier2SweepResult
from watson.privacy.spoolsweep import SpoolSweepResult


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


def test_summary_strips_uncited_body_sentences(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        runtime = FakeRuntime(
            complete_queue=[
                {"activities": []},
                {"entity_deltas": [], "decisions": [], "commitments": []},
                {
                    "paragraphs": [
                        {
                            "text": (
                                f"Uncited material stays out. "
                                f"Cited work landed [c:{seeded['observed']}]."
                            ),
                            "claim_ids": [seeded["observed"]],
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
        assert "Uncited material" not in narrative
        assert f"[c:{seeded['observed']}]" in narrative
        sections = json.loads(
            str(
                conn.execute(
                    "SELECT sections_json FROM daily_summaries WHERE working_day_id = ?",
                    (seeded["day_id"],),
                ).fetchone()[0]
            )
        )
        unverified_ids = {item["id"] for item in sections["unverified"]}
        assert seeded["inferred"] in unverified_ids
    finally:
        conn.close()


def test_summary_prompt_is_scoped_to_day_and_version(tmp_path: Path) -> None:
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
        v2 = _insert_claim(
            conn,
            conn.execute("SELECT id FROM observations LIMIT 1").fetchone()[0],
            seeded["day_id"],
            seeded["changed"],
            conn.execute("SELECT id FROM frames LIMIT 1").fetchone()[0],
            kind="activity",
            assertion="observed",
            text="v2 only",
            pipeline_version=2,
        )
        captured: list[str] = []

        class CaptureRuntime(FakeRuntime):
            def complete(self, messages, schema):  # type: ignore[no-untyped-def]
                captured.append("\n".join(item["content"] for item in messages))
                return super().complete(messages, schema)

        runtime = CaptureRuntime(recordings=RECORDINGS)
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        narrative_prompts = [blob for blob in captured if "Write the daily narrative" in blob]
        assert narrative_prompts
        prompt = narrative_prompts[0]
        assert other not in prompt
        assert v2 not in prompt
        assert seeded["observed"] in prompt
    finally:
        conn.close()


def test_summary_uses_configured_pipeline_version(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        for row in conn.execute(
            "SELECT id FROM segments WHERE working_day_id = ?", (seeded["day_id"],)
        ):
            _insert_observation(conn, str(row[0]), pipeline_version=2)
        _insert_claim(
            conn,
            conn.execute(
                "SELECT id FROM observations WHERE pipeline_version = 2 LIMIT 1"
            ).fetchone()[0],
            seeded["day_id"],
            seeded["changed"],
            conn.execute("SELECT id FROM frames LIMIT 1").fetchone()[0],
            kind="outcome",
            assertion="observed",
            text="v2 appendix",
            pipeline_version=2,
        )
        runtime = _runtime()
        consolidate_working_day(
            conn,
            seeded["day_id"],
            runtime=runtime,
            embedding=runtime,
            now=NOW,
            settings=ConsolidateSettings(pipeline_version=2),
        )
        row = conn.execute(
            """
            SELECT pipeline_version FROM daily_summaries
            WHERE working_day_id = ? AND pipeline_version = 2
            """,
            (seeded["day_id"],),
        ).fetchone()
        assert row is not None
        assert int(row["pipeline_version"]) == 2
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM daily_summaries WHERE pipeline_version = 1"
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


def test_summary_insert_failure_rolls_back_derived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)

        def boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("summary insert")

        monkeypatch.setattr("watson.pipeline.consolidation._insert_summary", boom)
        runtime = _runtime()
        with pytest.raises(RuntimeError, match="summary insert"):
            consolidate_working_day(
                conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW
            )
        counts = derived_counts(conn, seeded["day_id"])
        assert counts["activities"] == 0
        assert counts["summaries"] == 0
    finally:
        conn.close()


def test_verify_rereads_summary_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []

    def missing(*_a: object, **_k: object) -> None:
        order.append("verify")
        return None

    def rec_tier2(*_a: object, **_k: object) -> Tier2SweepResult:
        order.append("tier2")
        return Tier2SweepResult(0, 0, 0)

    def rec_spool(*_a: object, **_k: object) -> SpoolSweepResult:
        order.append("spool")
        return SpoolSweepResult(0, 0, 0, 0, 0, 0)

    monkeypatch.setattr("watson.pipeline.consolidation._reread_summary", missing)
    monkeypatch.setattr("watson.pipeline.consolidation.sweep_tier2", rec_tier2)
    monkeypatch.setattr("watson.pipeline.consolidation.sweep_spool", rec_spool)
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        runtime = _runtime()
        with pytest.raises(ConsolidationError, match="summary missing"):
            consolidate_working_day(
                conn,
                seeded["day_id"],
                runtime=runtime,
                embedding=runtime,
                spool_dir=tmp_path / "spool",
                now=NOW,
            )
        assert order == ["verify"]
    finally:
        conn.close()


def test_missing_runtime_holds_instead_of_faking(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        with pytest.raises(RuntimeUnavailable):
            consolidate_working_day(conn, seeded["day_id"], now=NOW)
        row = conn.execute(
            "SELECT state, consolidation_error FROM working_days WHERE id = ?",
            (seeded["day_id"],),
        ).fetchone()
        assert row["state"] == "closing"
        assert row["consolidation_error"] == "RuntimeUnavailable"
        assert conn.execute("SELECT COUNT(*) FROM daily_summaries").fetchone()[0] == 0
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

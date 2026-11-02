"""Additive claim/entity corrections: overlay, provenance, consolidation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from tests.replay_support import TEST_KEY
from tests.unit.test_consolidation import (
    NOW,
    RECORDINGS,
    _insert_claim,
    _insert_day,
    _insert_entity,
    _insert_frame,
    _insert_observation,
    _insert_segment,
    _open,
)
from watson.intelligence.runtime import FakeRuntime
from watson.intelligence.runtime.types import Message
from watson.memory.corrections import (
    MAX_NEW_TEXT,
    CorrectionError,
    apply_claim_corrections,
    claim_histories,
    merge_entities,
    overlay_from_history,
    record_claim_correction,
    rejected_claim_ids,
    rename_entity,
)
from watson.memory.migrate import upgrade_head
from watson.pipeline.consolidation import (
    _insert_achievements,
    _load_claims,
    consolidate_working_day,
)


class _EchoSummary(FakeRuntime):
    """Cite every claim the summarizer is given, using its effective text."""

    def complete(self, messages: Sequence[Message], schema: Mapping[str, Any]) -> dict[str, Any]:
        blob = "\n".join(item["content"] for item in messages)
        if "Write the daily narrative" in blob:
            parts: list[str] = []
            ids: list[str] = []
            for line in blob.splitlines():
                if not line.startswith("[c:"):
                    continue
                close = line.index("]")
                claim_id = line[3:close]
                text = line.split(") ", 1)[-1]
                parts.append(f"{text} [c:{claim_id}].")
                ids.append(claim_id)
            narrative = " ".join(parts) if parts else "Nothing happened."
            return {"paragraphs": [{"text": narrative, "claim_ids": ids}]}
        return super().complete(messages, schema)


def _echo() -> _EchoSummary:
    return _EchoSummary(recordings=RECORDINGS)


def _seed_claim(
    conn: object, *, text: str = "Appendix drafted", date: str = "2026-10-31"
) -> dict[str, str]:
    day_id = _insert_day(conn, date=date)
    frame_id = _insert_frame(conn, day_id)
    segment_id = _insert_segment(conn, day_id, frame_id)
    observation_id = _insert_observation(conn, segment_id)
    claim_id = _insert_claim(
        conn,
        observation_id,
        day_id,
        segment_id,
        frame_id,
        kind="outcome",
        assertion="observed",
        text=text,
        snippet="Still editing: Acme proposal appendix",
    )
    return {
        "day_id": day_id,
        "frame_id": frame_id,
        "segment_id": segment_id,
        "observation_id": observation_id,
        "claim_id": claim_id,
    }


def _evidence_rows(conn: object) -> list[tuple[object, ...]]:
    return [tuple(row) for row in conn.execute("SELECT * FROM claim_evidence ORDER BY id")]


def _claim_row(conn: object, claim_id: str) -> tuple[str, str]:
    row = conn.execute("SELECT text, assertion FROM claims WHERE id = ?", (claim_id,)).fetchone()
    return str(row[0]), str(row[1])


def test_correct_is_additive_and_keeps_evidence(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_claim(conn)
        before = _evidence_rows(conn)
        original_text, original_assertion = _claim_row(conn, seeded["claim_id"])
        record_claim_correction(conn, seeded["claim_id"], "correct", new_text="Appendix published")
        after_text, after_assertion = _claim_row(conn, seeded["claim_id"])
        assert after_text == original_text
        assert after_assertion == original_assertion
        assert _evidence_rows(conn) == before
        rows = conn.execute("SELECT action, new_text, target_id FROM corrections").fetchall()
        assert len(rows) == 1
        assert str(rows[0][0]) == "correct"
        assert str(rows[0][1]) == "Appendix published"
        assert str(rows[0][2]) == seeded["claim_id"]
        overlayed = apply_claim_corrections(
            conn, _load_claims(conn, seeded["day_id"], 1), working_day_id=seeded["day_id"]
        )
        assert overlayed[0]["text"] == "Appendix published"
        assert overlayed[0]["assertion"] == "user_corrected"
    finally:
        conn.close()


def test_confirm_reject_not_work_and_confirm_after_correct(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_claim(conn)
        claim_id = seeded["claim_id"]
        record_claim_correction(conn, claim_id, "confirm")
        overlayed = apply_claim_corrections(
            conn, _load_claims(conn, seeded["day_id"], 1), working_day_id=seeded["day_id"]
        )
        assert overlayed[0]["assertion"] == "user_confirmed"
        assert overlayed[0]["text"] == "Appendix drafted"
        record_claim_correction(conn, claim_id, "correct", new_text="Appendix published")
        record_claim_correction(conn, claim_id, "confirm")
        overlayed = apply_claim_corrections(
            conn, _load_claims(conn, seeded["day_id"], 1), working_day_id=seeded["day_id"]
        )
        assert overlayed[0]["assertion"] == "user_confirmed"
        assert overlayed[0]["text"] == "Appendix published"
        record_claim_correction(conn, claim_id, "reject")
        overlayed = apply_claim_corrections(
            conn, _load_claims(conn, seeded["day_id"], 1), working_day_id=seeded["day_id"]
        )
        assert overlayed == []
        assert claim_id in rejected_claim_ids(conn, working_day_id=seeded["day_id"])
        record_claim_correction(conn, claim_id, "not_work")
        assert conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 5
        history = claim_histories(conn, working_day_id=seeded["day_id"])[claim_id]
        text, assertion, latest = overlay_from_history("Appendix drafted", "observed", history)
        assert latest is not None and latest.action == "not_work"
        assert assertion == "user_rejected"
        assert text == "Appendix published"
        original_text, original_assertion = _claim_row(conn, claim_id)
        assert original_text == "Appendix drafted"
        assert original_assertion == "observed"
    finally:
        conn.close()


def test_correction_requires_text_and_known_claim(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_claim(conn)
        try:
            record_claim_correction(conn, seeded["claim_id"], "correct", new_text="   ")
        except CorrectionError as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError("blank correct should fail")
        try:
            record_claim_correction(conn, "missing-id", "confirm")
        except CorrectionError as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("missing claim should 404")
        try:
            record_claim_correction(conn, seeded["claim_id"], "explode")
        except CorrectionError as exc:
            assert "action" in str(exc)
        else:
            raise AssertionError("unknown action should fail")
    finally:
        conn.close()


def test_corrections_are_scoped_by_day_and_pipeline_version(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        first = _seed_claim(conn, text="day-one claim", date="2026-10-30")
        second_day = _insert_day(conn, date="2026-10-31")
        frame_id = _insert_frame(conn, second_day)
        segment_id = _insert_segment(conn, second_day, frame_id)
        observation_id = _insert_observation(conn, segment_id)
        v1 = _insert_claim(
            conn,
            observation_id,
            second_day,
            segment_id,
            frame_id,
            kind="activity",
            assertion="observed",
            text="today-v1 claim",
        )
        v2 = _insert_claim(
            conn,
            observation_id,
            second_day,
            segment_id,
            frame_id,
            kind="activity",
            assertion="observed",
            text="today-v2 claim",
            pipeline_version=2,
        )
        record_claim_correction(conn, first["claim_id"], "correct", new_text="day-one corrected")
        record_claim_correction(conn, v1, "reject")
        try:
            record_claim_correction(conn, v2, "correct", new_text="v2 overlay", pipeline_version=1)
        except CorrectionError as exc:
            assert exc.status_code == 404
        record_claim_correction(conn, v2, "correct", new_text="v2 overlay", pipeline_version=2)
        day1 = apply_claim_corrections(
            conn, _load_claims(conn, first["day_id"], 1), working_day_id=first["day_id"]
        )
        day2 = apply_claim_corrections(
            conn, _load_claims(conn, second_day, 1), working_day_id=second_day
        )
        day2_v2 = apply_claim_corrections(
            conn,
            _load_claims(conn, second_day, 2),
            working_day_id=second_day,
            pipeline_version=2,
        )
        assert day1[0]["text"] == "day-one corrected"
        assert day2 == []
        assert day2_v2[0]["text"] == "v2 overlay"
        assert v1 not in {row["id"] for row in day2_v2}
    finally:
        conn.close()


def test_reject_excludes_claim_from_summary_and_achievements(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_claim(conn, text="Appendix drafted")
        runtime = _echo()
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        before = str(conn.execute("SELECT narrative_md FROM daily_summaries").fetchone()[0])
        assert "Appendix drafted" in before
        assert seeded["claim_id"] in before
        record_claim_correction(conn, seeded["claim_id"], "reject")
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        after = str(conn.execute("SELECT narrative_md FROM daily_summaries").fetchone()[0])
        assert "Appendix drafted" not in after
        assert seeded["claim_id"] not in after
        _insert_achievements(
            conn, seeded["day_id"], pipeline_version=1, stamp="2026-10-31T18:30:00Z"
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM achievement_candidates").fetchone()[0] == 0
        assert _claim_row(conn, seeded["claim_id"])[0] == "Appendix drafted"
        assert conn.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 1
    finally:
        conn.close()


def test_correct_changes_subsequent_summary(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_claim(conn, text="WRONG claim text")
        runtime = _echo()
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        before = str(conn.execute("SELECT narrative_md FROM daily_summaries").fetchone()[0])
        assert "WRONG claim text" in before
        record_claim_correction(conn, seeded["claim_id"], "correct", new_text="Appendix published")
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        after = str(conn.execute("SELECT narrative_md FROM daily_summaries").fetchone()[0])
        assert "Appendix published" in after
        assert "WRONG claim text" not in after
        assert seeded["claim_id"] in after
    finally:
        conn.close()


def test_rename_keeps_old_name_as_alias(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        entity_id = _insert_entity(conn, name="Acme Q4 Proposal")
        rename_entity(conn, entity_id, "Acme Deal")
        row = conn.execute(
            "SELECT canonical_name, merged_into_id FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()
        assert str(row[0]) == "Acme Deal"
        assert row[1] is None
        aliases = {
            str(r[0])
            for r in conn.execute(
                "SELECT alias FROM entity_aliases WHERE entity_id = ?", (entity_id,)
            )
        }
        assert "Acme Q4 Proposal" in aliases
        action = str(conn.execute("SELECT action FROM corrections").fetchone()[0])
        assert action == "correct"
    finally:
        conn.close()


def test_merge_is_not_destructive(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        dest = _insert_entity(conn, name="Acme Q4 Proposal")
        source = _insert_entity(conn, name="Old Acme")
        conn.execute(
            """
            INSERT INTO entity_aliases (id, created_at, updated_at, entity_id, alias, source)
            VALUES ('alias-old', '2026-10-31T09:00:00Z', '2026-10-31T09:00:00Z', ?, 'Acme', 'model')
            """,
            (source,),
        )
        conn.commit()
        merge_entities(conn, source, dest)
        src = conn.execute(
            "SELECT canonical_name, merged_into_id FROM entities WHERE id = ?", (source,)
        ).fetchone()
        assert str(src[0]) == "Old Acme"
        assert str(src[1]) == dest
        dest_aliases = {
            str(r[0])
            for r in conn.execute("SELECT alias FROM entity_aliases WHERE entity_id = ?", (dest,))
        }
        source_aliases = {
            str(r[0])
            for r in conn.execute("SELECT alias FROM entity_aliases WHERE entity_id = ?", (source,))
        }
        assert {"Old Acme", "Acme"} <= dest_aliases
        assert "Acme" in source_aliases
        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 2
        assert str(conn.execute("SELECT action FROM corrections").fetchone()[0]) == "merge"
    finally:
        conn.close()


def test_merge_rejects_self_cycle_and_cross_type(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        dest = _insert_entity(conn, name="Zebra Deal")
        source = _insert_entity(conn, name="Acme")
        other = _insert_entity(conn, name="Other Deal")
        person = _insert_entity(conn, name="Priya Natarajan", typ="person")
        with pytest.raises(CorrectionError, match="cannot merge an entity into itself"):
            merge_entities(conn, dest, dest)
        with pytest.raises(CorrectionError, match="entities must be the same type"):
            merge_entities(conn, person, dest)
        with pytest.raises(CorrectionError, match="entities must be the same type"):
            merge_entities(conn, dest, person)
        merge_entities(conn, source, dest)
        with pytest.raises(CorrectionError, match="entity not found") as merged_source:
            merge_entities(conn, source, other)
        assert merged_source.value.status_code == 404
        with pytest.raises(CorrectionError, match="entity not found") as merged_dest:
            merge_entities(conn, other, source)
        assert merged_dest.value.status_code == 404
        assert (
            str(
                conn.execute(
                    "SELECT merged_into_id FROM entities WHERE id = ?", (source,)
                ).fetchone()[0]
            )
            == dest
        )
        assert (
            conn.execute("SELECT merged_into_id FROM entities WHERE id = ?", (dest,)).fetchone()[0]
            is None
        )
        assert (
            conn.execute("SELECT merged_into_id FROM entities WHERE id = ?", (other,)).fetchone()[0]
            is None
        )
        assert (
            conn.execute("SELECT merged_into_id FROM entities WHERE id = ?", (person,)).fetchone()[
                0
            ]
            is None
        )
    finally:
        conn.close()


def test_rename_rejects_name_clash_and_too_long(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        left = _insert_entity(conn, name="Acme Deal")
        right = _insert_entity(conn, name="Other Deal")
        with pytest.raises(CorrectionError, match="name already exists"):
            rename_entity(conn, right, "Acme Deal")
        assert (
            str(
                conn.execute(
                    "SELECT canonical_name FROM entities WHERE id = ?", (right,)
                ).fetchone()[0]
            )
            == "Other Deal"
        )
        with pytest.raises(CorrectionError, match="new_text is too long"):
            rename_entity(conn, right, "x" * (MAX_NEW_TEXT + 1))
        rename_entity(conn, right, "y" * MAX_NEW_TEXT)
        assert (
            str(
                conn.execute(
                    "SELECT canonical_name FROM entities WHERE id = ?", (right,)
                ).fetchone()[0]
            )
            == "y" * MAX_NEW_TEXT
        )
        assert (
            str(
                conn.execute(
                    "SELECT canonical_name FROM entities WHERE id = ?", (left,)
                ).fetchone()[0]
            )
            == "Acme Deal"
        )
        assert conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 1
    finally:
        conn.close()


def test_upgrade_creates_corrections_table(tmp_path: Path) -> None:
    path = tmp_path / "watson.db"
    assert upgrade_head(path, TEST_KEY) == "0010_m4b_corrections"

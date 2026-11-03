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
    match_claim_corrections,
    merge_entities,
    orphaned_corrections,
    overlay_from_history,
    record_claim_correction,
    rejected_claim_ids,
    rename_entity,
)
from watson.memory.ids import new_id
from watson.memory.migrate import upgrade_head
from watson.pipeline.consolidation import (
    ConsolidateSettings,
    _insert_achievements,
    _load_claims,
    consolidate_working_day,
)
from watson.pipeline.frames import PIPELINE_VERSION


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
            conn,
            _load_claims(conn, seeded["day_id"], PIPELINE_VERSION),
            working_day_id=seeded["day_id"],
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
            conn,
            _load_claims(conn, seeded["day_id"], PIPELINE_VERSION),
            working_day_id=seeded["day_id"],
        )
        assert overlayed[0]["assertion"] == "user_confirmed"
        assert overlayed[0]["text"] == "Appendix drafted"
        record_claim_correction(conn, claim_id, "correct", new_text="Appendix published")
        record_claim_correction(conn, claim_id, "confirm")
        overlayed = apply_claim_corrections(
            conn,
            _load_claims(conn, seeded["day_id"], PIPELINE_VERSION),
            working_day_id=seeded["day_id"],
        )
        assert overlayed[0]["assertion"] == "user_confirmed"
        assert overlayed[0]["text"] == "Appendix published"
        record_claim_correction(conn, claim_id, "reject")
        overlayed = apply_claim_corrections(
            conn,
            _load_claims(conn, seeded["day_id"], PIPELINE_VERSION),
            working_day_id=seeded["day_id"],
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
    other_version = 99
    conn = _open(tmp_path)
    try:
        first = _seed_claim(conn, text="day-one claim", date="2026-10-30")
        second_day = _insert_day(conn, date="2026-10-31")
        frame_id = _insert_frame(conn, second_day)
        segment_id = _insert_segment(conn, second_day, frame_id)
        observation_id = _insert_observation(conn, segment_id)
        current = _insert_claim(
            conn,
            observation_id,
            second_day,
            segment_id,
            frame_id,
            kind="activity",
            assertion="observed",
            text="today-current claim",
        )
        other = _insert_claim(
            conn,
            observation_id,
            second_day,
            segment_id,
            frame_id,
            kind="activity",
            assertion="observed",
            text="today-other claim",
            pipeline_version=other_version,
            snippet="Other-day OCR line",
        )
        record_claim_correction(conn, first["claim_id"], "correct", new_text="day-one corrected")
        record_claim_correction(conn, current, "reject")
        with pytest.raises(CorrectionError) as missing:
            record_claim_correction(
                conn,
                other,
                "correct",
                new_text="other overlay",
                pipeline_version=PIPELINE_VERSION,
            )
        assert missing.value.status_code == 404
        record_claim_correction(
            conn,
            other,
            "correct",
            new_text="other overlay",
            pipeline_version=other_version,
        )
        day1 = apply_claim_corrections(
            conn,
            _load_claims(conn, first["day_id"], PIPELINE_VERSION),
            working_day_id=first["day_id"],
        )
        day2 = apply_claim_corrections(
            conn,
            _load_claims(conn, second_day, PIPELINE_VERSION),
            working_day_id=second_day,
        )
        day2_other = apply_claim_corrections(
            conn,
            _load_claims(conn, second_day, other_version),
            working_day_id=second_day,
            pipeline_version=other_version,
        )
        assert day1[0]["text"] == "day-one corrected"
        assert day2 == []
        assert day2_other[0]["text"] == "other overlay"
        assert current not in {row["id"] for row in day2_other}
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
            conn, seeded["day_id"], pipeline_version=PIPELINE_VERSION, stamp="2026-10-31T18:30:00Z"
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


def test_correction_survives_pipeline_version_bump(tmp_path: Path) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        seeded = _seed_claim(conn, text="Appendix drafted")
        record_claim_correction(conn, seeded["claim_id"], "correct", new_text="Appendix published")
        bumped = _insert_claim(
            conn,
            seeded["observation_id"],
            seeded["day_id"],
            seeded["segment_id"],
            seeded["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Appendix drafted",
            pipeline_version=newer,
            snippet="Still editing: Acme proposal appendix",
        )
        overlayed = apply_claim_corrections(
            conn,
            _load_claims(conn, seeded["day_id"], newer),
            working_day_id=seeded["day_id"],
            pipeline_version=newer,
        )
        assert [row["id"] for row in overlayed] == [bumped]
        assert overlayed[0]["text"] == "Appendix published"
        assert overlayed[0]["assertion"] == "user_corrected"
        stored = conn.execute(
            "SELECT target_id, pipeline_version, new_text FROM corrections"
        ).fetchall()
        assert len(stored) == 1
        assert str(stored[0][0]) == seeded["claim_id"]
        assert int(stored[0][1]) == PIPELINE_VERSION
        assert str(stored[0][2]) == "Appendix published"
        assert (
            orphaned_corrections(conn, working_day_id=seeded["day_id"], pipeline_version=newer)
            == ()
        )
    finally:
        conn.close()


def test_orphaned_correction_is_flagged_not_deleted(tmp_path: Path) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        seeded = _seed_claim(conn, text="Appendix drafted")
        recorded = record_claim_correction(
            conn, seeded["claim_id"], "correct", new_text="Appendix published"
        )
        _insert_claim(
            conn,
            seeded["observation_id"],
            seeded["day_id"],
            seeded["segment_id"],
            seeded["frame_id"],
            kind="activity",
            assertion="observed",
            text="unrelated later claim",
            pipeline_version=newer,
            snippet="Different OCR line",
        )
        overlayed = apply_claim_corrections(
            conn,
            _load_claims(conn, seeded["day_id"], newer),
            working_day_id=seeded["day_id"],
            pipeline_version=newer,
        )
        assert overlayed[0]["text"] == "unrelated later claim"
        assert overlayed[0]["assertion"] == "observed"
        orphans = orphaned_corrections(
            conn, working_day_id=seeded["day_id"], pipeline_version=newer
        )
        assert [item.id for item in orphans] == [recorded.id]
        assert orphans[0].target_id == seeded["claim_id"]
        assert conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 1
        assert _claim_row(conn, seeded["claim_id"])[0] == "Appendix drafted"
    finally:
        conn.close()


def test_corrections_are_idempotent_across_reprocessing(tmp_path: Path) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        seeded = _seed_claim(conn, text="Appendix drafted")
        record_claim_correction(conn, seeded["claim_id"], "correct", new_text="Appendix published")
        runtime = _echo()
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        bumped = _insert_claim(
            conn,
            seeded["observation_id"],
            seeded["day_id"],
            seeded["segment_id"],
            seeded["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Appendix drafted",
            pipeline_version=newer,
            snippet="Still editing: Acme proposal appendix",
        )
        first = apply_claim_corrections(
            conn,
            _load_claims(conn, seeded["day_id"], newer),
            working_day_id=seeded["day_id"],
            pipeline_version=newer,
        )
        second = apply_claim_corrections(
            conn,
            _load_claims(conn, seeded["day_id"], newer),
            working_day_id=seeded["day_id"],
            pipeline_version=newer,
        )
        assert first == second
        assert first[0]["id"] == bumped
        assert first[0]["text"] == "Appendix published"
        assert conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 1
        histories, orphans = match_claim_corrections(
            conn,
            _load_claims(conn, seeded["day_id"], newer),
            working_day_id=seeded["day_id"],
            pipeline_version=newer,
        )
        assert orphans == ()
        assert len(histories[bumped]) == 1
    finally:
        conn.close()


def test_same_version_twin_claims_do_not_share_a_correction(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        first = _seed_claim(conn, text="Wrote intro")
        twin = _insert_claim(
            conn,
            first["observation_id"],
            first["day_id"],
            first["segment_id"],
            first["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Wrote intro",
            snippet="Still editing: Acme proposal appendix",
        )
        record_claim_correction(conn, first["claim_id"], "correct", new_text="Published intro")
        overlayed = apply_claim_corrections(
            conn,
            _load_claims(conn, first["day_id"], PIPELINE_VERSION),
            working_day_id=first["day_id"],
        )
        by_id = {str(row["id"]): row for row in overlayed}
        assert by_id[first["claim_id"]]["text"] == "Published intro"
        assert by_id[twin]["text"] == "Wrote intro"
        assert by_id[twin]["assertion"] == "observed"
        record_claim_correction(conn, first["claim_id"], "reject")
        after_reject = apply_claim_corrections(
            conn,
            _load_claims(conn, first["day_id"], PIPELINE_VERSION),
            working_day_id=first["day_id"],
        )
        ids = {str(row["id"]) for row in after_reject}
        assert first["claim_id"] not in ids
        assert twin in ids
        assert {str(row["id"]) for row in after_reject} == {twin}
        assert conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 2
    finally:
        conn.close()


def test_same_text_does_not_cross_projects(tmp_path: Path) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        acme_frame = _insert_frame(conn, day_id)
        beta_frame = _insert_frame(conn, day_id, captured_at="2026-10-31T11:00:00Z")
        acme_segment = _insert_segment(conn, day_id, acme_frame)
        beta_segment = _insert_segment(conn, day_id, beta_frame, started_at="2026-10-31T11:00:00Z")
        _insert_entity(conn, name="Acme")
        _insert_entity(conn, name="Beta")
        acme_obs = _insert_observation(conn, acme_segment, project_candidates=["Acme"])
        beta_obs = _insert_observation(conn, beta_segment, project_candidates=["Beta"])
        old_acme = _insert_claim(
            conn,
            acme_obs,
            day_id,
            acme_segment,
            acme_frame,
            kind="outcome",
            assertion="observed",
            text="Wrote intro",
            snippet="Acme intro OCR",
        )
        record_claim_correction(conn, old_acme, "correct", new_text="Shipped intro")
        new_acme = _insert_claim(
            conn,
            acme_obs,
            day_id,
            acme_segment,
            acme_frame,
            kind="outcome",
            assertion="observed",
            text="Wrote intro",
            pipeline_version=newer,
            snippet="Acme intro OCR",
        )
        new_beta = _insert_claim(
            conn,
            beta_obs,
            day_id,
            beta_segment,
            beta_frame,
            kind="outcome",
            assertion="observed",
            text="Wrote intro",
            pipeline_version=newer,
            snippet="Beta intro OCR",
        )
        overlayed = apply_claim_corrections(
            conn,
            _load_claims(conn, day_id, newer),
            working_day_id=day_id,
            pipeline_version=newer,
        )
        by_id = {str(row["id"]): row for row in overlayed}
        assert by_id[new_acme]["text"] == "Shipped intro"
        assert by_id[new_beta]["text"] == "Wrote intro"
        assert by_id[new_beta]["assertion"] == "observed"
        assert orphaned_corrections(conn, working_day_id=day_id, pipeline_version=newer) == ()
    finally:
        conn.close()


def test_shared_snippet_on_uncorrected_claim_does_not_rematch(tmp_path: Path) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        intro = _seed_claim(conn, text="Wrote intro")
        typo = _insert_claim(
            conn,
            intro["observation_id"],
            intro["day_id"],
            intro["segment_id"],
            intro["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Fixed typo",
            snippet="Still editing: Acme proposal appendix",
        )
        record_claim_correction(conn, intro["claim_id"], "reject")
        later_typo = _insert_claim(
            conn,
            intro["observation_id"],
            intro["day_id"],
            intro["segment_id"],
            intro["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Fixed typo",
            pipeline_version=newer,
            snippet="Still editing: Acme proposal appendix",
        )
        overlayed = apply_claim_corrections(
            conn,
            _load_claims(conn, intro["day_id"], newer),
            working_day_id=intro["day_id"],
            pipeline_version=newer,
        )
        assert [str(row["id"]) for row in overlayed] == [later_typo]
        assert overlayed[0]["text"] == "Fixed typo"
        assert overlayed[0]["assertion"] == "observed"
        orphans = orphaned_corrections(conn, working_day_id=intro["day_id"], pipeline_version=newer)
        assert len(orphans) == 1
        assert orphans[0].target_id == intro["claim_id"]
        assert orphans[0].action == "reject"
        assert typo not in {str(row["id"]) for row in overlayed}
    finally:
        conn.close()


def test_two_to_one_cross_version_is_orphaned(tmp_path: Path) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        first = _seed_claim(conn, text="Wrote intro")
        _insert_claim(
            conn,
            first["observation_id"],
            first["day_id"],
            first["segment_id"],
            first["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Wrote intro",
            snippet="Still editing: Acme proposal appendix",
        )
        recorded = record_claim_correction(
            conn, first["claim_id"], "correct", new_text="Published intro"
        )
        later = _insert_claim(
            conn,
            first["observation_id"],
            first["day_id"],
            first["segment_id"],
            first["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Wrote intro",
            pipeline_version=newer,
            snippet="Still editing: Acme proposal appendix",
        )
        overlayed = apply_claim_corrections(
            conn,
            _load_claims(conn, first["day_id"], newer),
            working_day_id=first["day_id"],
            pipeline_version=newer,
        )
        assert overlayed[0]["id"] == later
        assert overlayed[0]["text"] == "Wrote intro"
        assert overlayed[0]["assertion"] == "observed"
        orphans = orphaned_corrections(conn, working_day_id=first["day_id"], pipeline_version=newer)
        assert [item.id for item in orphans] == [recorded.id]
        assert conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 1
    finally:
        conn.close()


def _seed_scoped_claim(
    conn: object,
    *,
    text: str,
    snippet: str,
    day_id: str | None = None,
    pipeline_version: int = PIPELINE_VERSION,
    project: str | None = None,
    person: str | None = None,
    captured_at: str = "2026-10-31T09:00:00Z",
    kind: str = "outcome",
) -> dict[str, str]:
    if day_id is None:
        day_id = _insert_day(conn)
    frame_id = _insert_frame(conn, day_id, captured_at=captured_at)
    segment_id = _insert_segment(conn, day_id, frame_id, started_at=captured_at)
    observation_id = _insert_observation(
        conn,
        segment_id,
        project_candidates=None if project is None else [project],
        people_candidates=None if person is None else [person],
    )
    claim_id = _insert_claim(
        conn,
        observation_id,
        day_id,
        segment_id,
        frame_id,
        kind=kind,
        assertion="observed",
        text=text,
        pipeline_version=pipeline_version,
        snippet=snippet,
        created_at=captured_at,
    )
    return {
        "day_id": day_id,
        "frame_id": frame_id,
        "segment_id": segment_id,
        "observation_id": observation_id,
        "claim_id": claim_id,
    }


def _overlay_by_id(
    conn: object, day_id: str, pipeline_version: int
) -> dict[str, dict[str, object]]:
    rows = apply_claim_corrections(
        conn,
        _load_claims(conn, day_id, pipeline_version),
        working_day_id=day_id,
        pipeline_version=pipeline_version,
    )
    return {str(row["id"]): row for row in rows}


def test_project_and_person_scope_keep_same_text_and_snippet_apart(tmp_path: Path) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        _insert_entity(conn, name="Acme")
        _insert_entity(conn, name="Beta")
        _insert_entity(conn, name="Priya", typ="person")
        _insert_entity(conn, name="Jordan", typ="person")
        old_acme = _seed_scoped_claim(
            conn, text="Wrote intro", snippet="Shared OCR line", project="Acme"
        )
        day_id = old_acme["day_id"]
        old_priya = _seed_scoped_claim(
            conn,
            text="Wrote intro",
            snippet="Shared OCR line",
            day_id=day_id,
            person="Priya",
            captured_at="2026-10-31T10:00:00Z",
        )
        record_claim_correction(conn, old_acme["claim_id"], "correct", new_text="Shipped intro")
        record_claim_correction(conn, old_priya["claim_id"], "correct", new_text="Priya shipped")
        new_acme = _seed_scoped_claim(
            conn,
            text="Wrote intro",
            snippet="Shared OCR line",
            day_id=day_id,
            pipeline_version=newer,
            project="Acme",
            captured_at="2026-10-31T11:00:00Z",
        )
        new_beta = _seed_scoped_claim(
            conn,
            text="Wrote intro",
            snippet="Shared OCR line",
            day_id=day_id,
            pipeline_version=newer,
            project="Beta",
            captured_at="2026-10-31T12:00:00Z",
        )
        new_priya = _seed_scoped_claim(
            conn,
            text="Wrote intro",
            snippet="Shared OCR line",
            day_id=day_id,
            pipeline_version=newer,
            person="Priya",
            captured_at="2026-10-31T13:00:00Z",
        )
        new_jordan = _seed_scoped_claim(
            conn,
            text="Wrote intro",
            snippet="Shared OCR line",
            day_id=day_id,
            pipeline_version=newer,
            person="Jordan",
            captured_at="2026-10-31T14:00:00Z",
        )
        by_id = _overlay_by_id(conn, day_id, newer)
        assert by_id[new_acme["claim_id"]]["text"] == "Shipped intro"
        assert by_id[new_beta["claim_id"]]["text"] == "Wrote intro"
        assert by_id[new_priya["claim_id"]]["text"] == "Priya shipped"
        assert by_id[new_jordan["claim_id"]]["text"] == "Wrote intro"
        assert orphaned_corrections(conn, working_day_id=day_id, pipeline_version=newer) == ()
    finally:
        conn.close()


def _assert_merged_scope_rematch(tmp_path: Path, *, source_name: str, dest_name: str) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        dest = _insert_entity(conn, name=dest_name)
        source = _insert_entity(conn, name=source_name)
        old = _seed_scoped_claim(
            conn, text="Wrote intro", snippet="Acme intro OCR", project=source_name
        )
        record_claim_correction(conn, old["claim_id"], "correct", new_text="Shipped intro")
        merge_entities(conn, source, dest)
        later = _seed_scoped_claim(
            conn,
            text="Wrote intro",
            snippet="Acme intro OCR",
            day_id=old["day_id"],
            pipeline_version=newer,
            project=dest_name,
            captured_at="2026-10-31T11:00:00Z",
        )
        by_id = _overlay_by_id(conn, old["day_id"], newer)
        assert by_id[later["claim_id"]]["text"] == "Shipped intro"
        assert (
            orphaned_corrections(conn, working_day_id=old["day_id"], pipeline_version=newer) == ()
        )
    finally:
        conn.close()


def test_merged_entity_scope_still_rematches(tmp_path: Path) -> None:
    _assert_merged_scope_rematch(tmp_path, source_name="Old Acme", dest_name="Acme")


def test_merged_entity_scope_still_rematches_reversed_names(tmp_path: Path) -> None:
    _assert_merged_scope_rematch(tmp_path, source_name="Acme", dest_name="Acme Ltd")


def test_unresolved_scope_names_do_not_collapse(tmp_path: Path) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        old = _seed_scoped_claim(
            conn, text="Wrote intro", snippet="Shared OCR line", project="Acme"
        )
        record_claim_correction(conn, old["claim_id"], "correct", new_text="Shipped intro")
        new_acme = _seed_scoped_claim(
            conn,
            text="Wrote intro",
            snippet="Shared OCR line",
            day_id=old["day_id"],
            pipeline_version=newer,
            project="Acme",
            captured_at="2026-10-31T11:00:00Z",
        )
        new_beta = _seed_scoped_claim(
            conn,
            text="Wrote intro",
            snippet="Shared OCR line",
            day_id=old["day_id"],
            pipeline_version=newer,
            project="Beta",
            captured_at="2026-10-31T12:00:00Z",
        )
        by_id = _overlay_by_id(conn, old["day_id"], newer)
        assert by_id[new_acme["claim_id"]]["text"] == "Shipped intro"
        assert by_id[new_beta["claim_id"]]["text"] == "Wrote intro"
        assert by_id[new_beta["claim_id"]]["assertion"] == "observed"
        assert (
            orphaned_corrections(conn, working_day_id=old["day_id"], pipeline_version=newer) == ()
        )
    finally:
        conn.close()


def test_s8_text_path_rematch_alone(tmp_path: Path) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        old = _seed_claim(conn, text="Wrote intro")
        record_claim_correction(conn, old["claim_id"], "correct", new_text="Published intro")
        later = _insert_claim(
            conn,
            old["observation_id"],
            old["day_id"],
            old["segment_id"],
            old["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Wrote intro",
            pipeline_version=newer,
            snippet="Different OCR line after re-understand",
        )
        by_id = _overlay_by_id(conn, old["day_id"], newer)
        assert by_id[later]["text"] == "Published intro"
        assert by_id[later]["assertion"] == "user_corrected"
        assert (
            orphaned_corrections(conn, working_day_id=old["day_id"], pipeline_version=newer) == ()
        )
    finally:
        conn.close()


def test_s9_evidence_path_rematch_alone(tmp_path: Path) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        old = _seed_claim(conn, text="Wrote intro")
        record_claim_correction(conn, old["claim_id"], "correct", new_text="Published intro")
        later = _insert_claim(
            conn,
            old["observation_id"],
            old["day_id"],
            old["segment_id"],
            old["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Drafted the introduction",
            pipeline_version=newer,
            snippet="Still editing: Acme proposal appendix",
        )
        by_id = _overlay_by_id(conn, old["day_id"], newer)
        assert by_id[later]["text"] == "Published intro"
        assert by_id[later]["assertion"] == "user_corrected"
        assert (
            orphaned_corrections(conn, working_day_id=old["day_id"], pipeline_version=newer) == ()
        )
    finally:
        conn.close()


def test_s10_new_side_uniqueness_orphans(tmp_path: Path) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        old = _seed_claim(conn, text="Wrote intro")
        recorded = record_claim_correction(
            conn, old["claim_id"], "correct", new_text="Published intro"
        )
        first = _insert_claim(
            conn,
            old["observation_id"],
            old["day_id"],
            old["segment_id"],
            old["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Wrote intro",
            pipeline_version=newer,
            snippet="Still editing: Acme proposal appendix",
        )
        second = _insert_claim(
            conn,
            old["observation_id"],
            old["day_id"],
            old["segment_id"],
            old["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Wrote intro",
            pipeline_version=newer,
            snippet="Still editing: Acme proposal appendix",
        )
        by_id = _overlay_by_id(conn, old["day_id"], newer)
        assert by_id[first]["text"] == "Wrote intro"
        assert by_id[second]["text"] == "Wrote intro"
        orphans = orphaned_corrections(conn, working_day_id=old["day_id"], pipeline_version=newer)
        assert [item.id for item in orphans] == [recorded.id]
    finally:
        conn.close()


def test_s11_text_vs_evidence_disagreement_orphans(tmp_path: Path) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        old = _seed_claim(conn, text="Wrote intro")
        recorded = record_claim_correction(
            conn, old["claim_id"], "correct", new_text="Published intro"
        )
        text_hit = _insert_claim(
            conn,
            old["observation_id"],
            old["day_id"],
            old["segment_id"],
            old["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Wrote intro",
            pipeline_version=newer,
            snippet="Different OCR line",
        )
        evidence_hit = _insert_claim(
            conn,
            old["observation_id"],
            old["day_id"],
            old["segment_id"],
            old["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Fixed typo",
            pipeline_version=newer,
            snippet="Still editing: Acme proposal appendix",
        )
        by_id = _overlay_by_id(conn, old["day_id"], newer)
        assert by_id[text_hit]["text"] == "Wrote intro"
        assert by_id[evidence_hit]["text"] == "Fixed typo"
        orphans = orphaned_corrections(conn, working_day_id=old["day_id"], pipeline_version=newer)
        assert [item.id for item in orphans] == [recorded.id]
    finally:
        conn.close()


def test_s12_many_to_one_orphans_all_corrections(tmp_path: Path) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        intro = _seed_claim(conn, text="Wrote intro")
        typo = _insert_claim(
            conn,
            intro["observation_id"],
            intro["day_id"],
            intro["segment_id"],
            intro["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Fixed typo",
            snippet="Typo OCR line",
        )
        intro_fix = record_claim_correction(
            conn, intro["claim_id"], "correct", new_text="Published intro"
        )
        typo_reject = record_claim_correction(conn, typo, "reject")
        later = _insert_claim(
            conn,
            intro["observation_id"],
            intro["day_id"],
            intro["segment_id"],
            intro["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Wrote intro",
            pipeline_version=newer,
            snippet="Typo OCR line",
        )
        overlayed = apply_claim_corrections(
            conn,
            _load_claims(conn, intro["day_id"], newer),
            working_day_id=intro["day_id"],
            pipeline_version=newer,
        )
        assert [str(row["id"]) for row in overlayed] == [later]
        assert overlayed[0]["text"] == "Wrote intro"
        assert overlayed[0]["assertion"] == "observed"
        orphans = orphaned_corrections(conn, working_day_id=intro["day_id"], pipeline_version=newer)
        assert {item.id for item in orphans} == {intro_fix.id, typo_reject.id}
        stored = [
            tuple(row)
            for row in conn.execute(
                "SELECT target_id, action, pipeline_version FROM corrections ORDER BY rowid"
            )
        ]
        assert stored == [
            (intro["claim_id"], "correct", PIPELINE_VERSION),
            (typo, "reject", PIPELINE_VERSION),
        ]
    finally:
        conn.close()


def test_rejected_claim_skip_survives_version_bump(tmp_path: Path) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        seeded = _seed_claim(conn, text="Appendix drafted")
        runtime = _echo()
        record_claim_correction(conn, seeded["claim_id"], "reject")
        later = _insert_claim(
            conn,
            seeded["observation_id"],
            seeded["day_id"],
            seeded["segment_id"],
            seeded["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Appendix drafted",
            pipeline_version=newer,
            snippet="Still editing: Acme proposal appendix",
        )
        settings = ConsolidateSettings(pipeline_version=newer)
        consolidate_working_day(
            conn,
            seeded["day_id"],
            runtime=runtime,
            embedding=runtime,
            now=NOW,
            settings=settings,
        )
        after = str(conn.execute("SELECT narrative_md FROM daily_summaries").fetchone()[0])
        assert "Appendix drafted" not in after
        assert later not in after
        assert seeded["claim_id"] not in after
        _insert_achievements(
            conn, seeded["day_id"], pipeline_version=newer, stamp="2026-10-31T18:30:00Z"
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM achievement_candidates").fetchone()[0] == 0
        assert later in rejected_claim_ids(
            conn, working_day_id=seeded["day_id"], pipeline_version=newer
        )
        assert conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 1
        assert _claim_row(conn, seeded["claim_id"])[0] == "Appendix drafted"
    finally:
        conn.close()


def test_entity_correction_orphan_is_flagged_not_deleted(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_claim(conn)
        live = _insert_entity(conn, name="Acme Deal")
        rename_entity(conn, live, "Acme Live")
        stamp = "2026-10-31T12:00:00Z"
        ghost_id = new_id()
        conn.execute(
            """
            INSERT INTO corrections (
                id, created_at, updated_at, target_type, target_id, action, new_text,
                previous_assertion, working_day_id, pipeline_version
            ) VALUES (?, ?, ?, 'entity', 'missing-entity', 'correct', 'Ghost', NULL, NULL, ?)
            """,
            (ghost_id, stamp, stamp, PIPELINE_VERSION),
        )
        conn.commit()
        orphans = orphaned_corrections(conn, working_day_id=seeded["day_id"])
        assert [item.id for item in orphans] == [ghost_id]
        assert orphans[0].target_type == "entity"
        assert orphans[0].target_id == "missing-entity"
        assert conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 2
        assert (
            str(
                conn.execute(
                    "SELECT canonical_name FROM entities WHERE id = ?", (live,)
                ).fetchone()[0]
            )
            == "Acme Live"
        )
    finally:
        conn.close()


def test_case_folding_rematches_across_versions(tmp_path: Path) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        old = _seed_scoped_claim(conn, text="Wrote Intro", snippet="OCR Line")
        record_claim_correction(conn, old["claim_id"], "correct", new_text="Published intro")
        later = _insert_claim(
            conn,
            old["observation_id"],
            old["day_id"],
            old["segment_id"],
            old["frame_id"],
            kind="outcome",
            assertion="observed",
            text="wrote intro",
            pipeline_version=newer,
            snippet="ocr line",
        )
        by_id = _overlay_by_id(conn, old["day_id"], newer)
        assert by_id[later]["text"] == "Published intro"
        assert (
            orphaned_corrections(conn, working_day_id=old["day_id"], pipeline_version=newer) == ()
        )
    finally:
        conn.close()


def _later_claim(
    conn: object,
    seeded: dict[str, str],
    *,
    text: str,
    snippet: str,
    pipeline_version: int,
) -> str:
    return _insert_claim(
        conn,
        seeded["observation_id"],
        seeded["day_id"],
        seeded["segment_id"],
        seeded["frame_id"],
        kind="outcome",
        assertion="observed",
        text=text,
        pipeline_version=pipeline_version,
        snippet=snippet,
    )


def test_s18_two_bumps_stack_same_lineage(tmp_path: Path) -> None:
    mid = PIPELINE_VERSION + 1
    newest = PIPELINE_VERSION + 2
    snippet = "Still editing: Acme proposal appendix"
    conn = _open(tmp_path)
    try:
        intro = _seed_claim(conn, text="Wrote intro")
        typo = _insert_claim(
            conn,
            intro["observation_id"],
            intro["day_id"],
            intro["segment_id"],
            intro["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Fixed typo",
            snippet="Typo OCR line",
        )
        record_claim_correction(conn, intro["claim_id"], "correct", new_text="Published intro")
        record_claim_correction(conn, typo, "reject")
        mid_intro = _later_claim(
            conn, intro, text="Wrote intro", snippet=snippet, pipeline_version=mid
        )
        _later_claim(conn, intro, text="Fixed typo", snippet="Typo OCR line", pipeline_version=mid)
        record_claim_correction(conn, mid_intro, "confirm", pipeline_version=mid)
        later_intro = _later_claim(
            conn, intro, text="Wrote intro", snippet=snippet, pipeline_version=newest
        )
        later_typo = _later_claim(
            conn, intro, text="Fixed typo", snippet="Typo OCR line", pipeline_version=newest
        )
        overlayed = apply_claim_corrections(
            conn,
            _load_claims(conn, intro["day_id"], newest),
            working_day_id=intro["day_id"],
            pipeline_version=newest,
        )
        by_id = {str(row["id"]): row for row in overlayed}
        assert later_intro in by_id
        assert later_typo not in by_id
        assert by_id[later_intro]["text"] == "Published intro"
        assert by_id[later_intro]["assertion"] == "user_confirmed"
        histories, orphans = match_claim_corrections(
            conn,
            _load_claims(conn, intro["day_id"], newest),
            working_day_id=intro["day_id"],
            pipeline_version=newest,
        )
        assert orphans == ()
        assert [item.action for item in histories[later_intro]] == ["correct", "confirm"]
        assert later_typo in rejected_claim_ids(
            conn, working_day_id=intro["day_id"], pipeline_version=newest
        )
        stored = conn.execute(
            "SELECT target_id, action, pipeline_version FROM corrections ORDER BY rowid"
        ).fetchall()
        assert [(str(row[0]), str(row[1]), int(row[2])) for row in stored] == [
            (intro["claim_id"], "correct", PIPELINE_VERSION),
            (typo, "reject", PIPELINE_VERSION),
            (mid_intro, "confirm", mid),
        ]
    finally:
        conn.close()


def test_s18b_unrelated_mid_correction_does_not_orphan(tmp_path: Path) -> None:
    mid = PIPELINE_VERSION + 1
    newest = PIPELINE_VERSION + 2
    snippet = "Still editing: Acme proposal appendix"
    conn = _open(tmp_path)
    try:
        intro = _seed_claim(conn, text="Wrote intro")
        record_claim_correction(conn, intro["claim_id"], "reject")
        _later_claim(conn, intro, text="Wrote intro", snippet=snippet, pipeline_version=mid)
        other = _insert_claim(
            conn,
            intro["observation_id"],
            intro["day_id"],
            intro["segment_id"],
            intro["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Unrelated note",
            pipeline_version=mid,
            snippet="Other OCR line",
        )
        record_claim_correction(conn, other, "confirm", pipeline_version=mid)
        later_intro = _later_claim(
            conn, intro, text="Wrote intro", snippet=snippet, pipeline_version=newest
        )
        later_other = _later_claim(
            conn, intro, text="Unrelated note", snippet="Other OCR line", pipeline_version=newest
        )
        overlayed = apply_claim_corrections(
            conn,
            _load_claims(conn, intro["day_id"], newest),
            working_day_id=intro["day_id"],
            pipeline_version=newest,
        )
        assert [str(row["id"]) for row in overlayed] == [later_other]
        assert overlayed[0]["text"] == "Unrelated note"
        assert overlayed[0]["assertion"] == "user_confirmed"
        assert later_intro in rejected_claim_ids(
            conn, working_day_id=intro["day_id"], pipeline_version=newest
        )
        assert (
            orphaned_corrections(conn, working_day_id=intro["day_id"], pipeline_version=newest)
            == ()
        )
        assert conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 2
    finally:
        conn.close()


def _add_alias(conn: object, entity_id: str, alias: str) -> None:
    stamp = "2026-10-31T09:00:00Z"
    conn.execute(
        """
        INSERT INTO entity_aliases (id, created_at, updated_at, entity_id, alias, source)
        VALUES (?, ?, ?, ?, ?, 'user')
        """,
        (new_id(), stamp, stamp, entity_id, alias),
    )
    conn.commit()


def test_s22_l1_different_lineages_orphan_both(tmp_path: Path) -> None:
    mid = PIPELINE_VERSION + 1
    newest = PIPELINE_VERSION + 2
    conn = _open(tmp_path)
    try:
        intro = _seed_claim(conn, text="Wrote intro")
        intro_fix = record_claim_correction(
            conn, intro["claim_id"], "correct", new_text="Published intro"
        )
        typo = _insert_claim(
            conn,
            intro["observation_id"],
            intro["day_id"],
            intro["segment_id"],
            intro["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Fixed typo",
            pipeline_version=mid,
            snippet="Typo OCR line",
        )
        typo_reject = record_claim_correction(conn, typo, "reject", pipeline_version=mid)
        later = _later_claim(
            conn,
            intro,
            text="Wrote intro",
            snippet="Typo OCR line",
            pipeline_version=newest,
        )
        overlayed = apply_claim_corrections(
            conn,
            _load_claims(conn, intro["day_id"], newest),
            working_day_id=intro["day_id"],
            pipeline_version=newest,
        )
        assert [str(row["id"]) for row in overlayed] == [later]
        assert overlayed[0]["text"] == "Wrote intro"
        assert overlayed[0]["assertion"] == "observed"
        histories, orphans = match_claim_corrections(
            conn,
            _load_claims(conn, intro["day_id"], newest),
            working_day_id=intro["day_id"],
            pipeline_version=newest,
        )
        assert histories[later] == ()
        assert {item.id for item in orphans} == {intro_fix.id, typo_reject.id}
        assert conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 2
    finally:
        conn.close()


def test_s23_l3_second_lineage_pair_orphans_the_stack(tmp_path: Path) -> None:
    v1 = PIPELINE_VERSION + 1
    v2 = PIPELINE_VERSION + 2
    current = PIPELINE_VERSION + 3
    snippet = "Still editing: Acme proposal appendix"
    conn = _open(tmp_path)
    try:
        intro = _seed_claim(conn, text="Wrote intro")
        first = record_claim_correction(
            conn, intro["claim_id"], "correct", new_text="Published intro"
        )
        mid = _later_claim(conn, intro, text="Wrote intro", snippet=snippet, pipeline_version=v1)
        second = record_claim_correction(conn, mid, "confirm", pipeline_version=v1)
        typo = _insert_claim(
            conn,
            intro["observation_id"],
            intro["day_id"],
            intro["segment_id"],
            intro["frame_id"],
            kind="outcome",
            assertion="observed",
            text="Fixed typo",
            pipeline_version=v2,
            snippet="Typo OCR line",
        )
        third = record_claim_correction(conn, typo, "reject", pipeline_version=v2)
        later = _later_claim(
            conn, intro, text="Wrote intro", snippet="Typo OCR line", pipeline_version=current
        )
        overlayed = apply_claim_corrections(
            conn,
            _load_claims(conn, intro["day_id"], current),
            working_day_id=intro["day_id"],
            pipeline_version=current,
        )
        assert [str(row["id"]) for row in overlayed] == [later]
        assert overlayed[0]["text"] == "Wrote intro"
        assert overlayed[0]["assertion"] == "observed"
        histories, orphans = match_claim_corrections(
            conn,
            _load_claims(conn, intro["day_id"], current),
            working_day_id=intro["day_id"],
            pipeline_version=current,
        )
        assert histories[later] == ()
        assert {item.id for item in orphans} == {first.id, second.id, third.id}
        assert later not in rejected_claim_ids(
            conn, working_day_id=intro["day_id"], pipeline_version=current
        )
        assert conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 3
    finally:
        conn.close()


def test_s26_o1_reject_then_edit_keeps_the_edit(tmp_path: Path) -> None:
    mid = PIPELINE_VERSION + 1
    newest = PIPELINE_VERSION + 2
    snippet = "Still editing: Acme proposal appendix"
    conn = _open(tmp_path)
    try:
        intro = _seed_claim(conn, text="Wrote intro")
        rejected = record_claim_correction(conn, intro["claim_id"], "reject")
        mid_intro = _later_claim(
            conn, intro, text="Wrote intro", snippet=snippet, pipeline_version=mid
        )
        edited = record_claim_correction(
            conn, mid_intro, "correct", new_text="Published intro", pipeline_version=mid
        )
        conn.execute(
            "UPDATE corrections SET id = 'zzz-o1-reject', created_at = '2026-10-31T10:00:00Z' "
            "WHERE id = ?",
            (rejected.id,),
        )
        conn.execute(
            "UPDATE corrections SET id = 'aaa-o1-correct', created_at = '2026-10-31T11:00:00Z' "
            "WHERE id = ?",
            (edited.id,),
        )
        conn.commit()
        later = _later_claim(
            conn, intro, text="Wrote intro", snippet=snippet, pipeline_version=newest
        )
        overlayed = apply_claim_corrections(
            conn,
            _load_claims(conn, intro["day_id"], newest),
            working_day_id=intro["day_id"],
            pipeline_version=newest,
        )
        assert [str(row["id"]) for row in overlayed] == [later]
        assert overlayed[0]["text"] == "Published intro"
        assert overlayed[0]["assertion"] == "user_corrected"
        histories, orphans = match_claim_corrections(
            conn,
            _load_claims(conn, intro["day_id"], newest),
            working_day_id=intro["day_id"],
            pipeline_version=newest,
        )
        assert orphans == ()
        assert [item.action for item in histories[later]] == ["reject", "correct"]
        assert histories[later][-1].action == "correct"
        assert later not in rejected_claim_ids(
            conn, working_day_id=intro["day_id"], pipeline_version=newest
        )
    finally:
        conn.close()


def test_s26b_o1_reject_then_edit_read_at_v1(tmp_path: Path) -> None:
    mid = PIPELINE_VERSION + 1
    snippet = "Still editing: Acme proposal appendix"
    conn = _open(tmp_path)
    try:
        intro = _seed_claim(conn, text="Wrote intro")
        record_claim_correction(conn, intro["claim_id"], "reject")
        mid_intro = _later_claim(
            conn, intro, text="Wrote intro", snippet=snippet, pipeline_version=mid
        )
        record_claim_correction(
            conn, mid_intro, "correct", new_text="Published intro", pipeline_version=mid
        )
        overlayed = apply_claim_corrections(
            conn,
            _load_claims(conn, intro["day_id"], mid),
            working_day_id=intro["day_id"],
            pipeline_version=mid,
        )
        assert [str(row["id"]) for row in overlayed] == [mid_intro]
        assert overlayed[0]["text"] == "Published intro"
        assert overlayed[0]["assertion"] == "user_corrected"
        histories, orphans = match_claim_corrections(
            conn,
            _load_claims(conn, intro["day_id"], mid),
            working_day_id=intro["day_id"],
            pipeline_version=mid,
        )
        assert orphans == ()
        assert [item.action for item in histories[mid_intro]] == ["reject", "correct"]
        assert histories[mid_intro][-1].action == "correct"
        assert mid_intro not in rejected_claim_ids(
            conn, working_day_id=intro["day_id"], pipeline_version=mid
        )
    finally:
        conn.close()


def test_s27_h1_merge_chain_rematches_under_root(tmp_path: Path) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        first = _insert_entity(conn, name="Acme A")
        middle = _insert_entity(conn, name="Acme B")
        root = _insert_entity(conn, name="Acme C")
        old = _seed_scoped_claim(
            conn, text="Wrote intro", snippet="Acme intro OCR", project="Acme A"
        )
        record_claim_correction(conn, old["claim_id"], "correct", new_text="Shipped intro")
        merge_entities(conn, first, middle)
        merge_entities(conn, middle, root)
        later = _seed_scoped_claim(
            conn,
            text="Wrote intro",
            snippet="Acme intro OCR",
            day_id=old["day_id"],
            pipeline_version=newer,
            project="Acme C",
            captured_at="2026-10-31T11:00:00Z",
        )
        by_id = _overlay_by_id(conn, old["day_id"], newer)
        assert by_id[later["claim_id"]]["text"] == "Shipped intro"
        assert by_id[later["claim_id"]]["assertion"] == "user_corrected"
        assert (
            orphaned_corrections(conn, working_day_id=old["day_id"], pipeline_version=newer) == ()
        )
    finally:
        conn.close()


def test_s28_h3_ambiguous_alias_orphans(tmp_path: Path) -> None:
    newer = PIPELINE_VERSION + 1
    conn = _open(tmp_path)
    try:
        alpha = _insert_entity(conn, name="Alpha")
        beta = _insert_entity(conn, name="Beta")
        _add_alias(conn, alpha, "Acme")
        _add_alias(conn, beta, "Acme")
        old = _seed_scoped_claim(
            conn, text="Wrote intro", snippet="Shared OCR line", project="Acme"
        )
        recorded = record_claim_correction(
            conn, old["claim_id"], "correct", new_text="Shipped intro"
        )
        new_alpha = _seed_scoped_claim(
            conn,
            text="Wrote intro",
            snippet="Shared OCR line",
            day_id=old["day_id"],
            pipeline_version=newer,
            project="Alpha",
            captured_at="2026-10-31T11:00:00Z",
        )
        new_beta = _seed_scoped_claim(
            conn,
            text="Wrote intro",
            snippet="Shared OCR line",
            day_id=old["day_id"],
            pipeline_version=newer,
            project="Beta",
            captured_at="2026-10-31T12:00:00Z",
        )
        by_id = _overlay_by_id(conn, old["day_id"], newer)
        assert by_id[new_alpha["claim_id"]]["text"] == "Wrote intro"
        assert by_id[new_beta["claim_id"]]["text"] == "Wrote intro"
        orphans = orphaned_corrections(conn, working_day_id=old["day_id"], pipeline_version=newer)
        assert [item.id for item in orphans] == [recorded.id]
        assert conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 1
    finally:
        conn.close()


def test_upgrade_creates_corrections_table(tmp_path: Path) -> None:
    path = tmp_path / "watson.db"
    assert upgrade_head(path, TEST_KEY) == "0010_m4b_corrections"

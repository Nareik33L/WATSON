"""Day consolidation: idempotent replace, observed-only achievements, failure, catch-up."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.replay_support import TEST_KEY
from watson.config.schema import DEFAULT_DAY_END
from watson.intelligence.runtime import (
    UNDERSTAND_JOB_KIND,
    FakeRuntime,
    JobFailure,
    RuntimeUnavailable,
)
from watson.intelligence.schemas.consolidation import DayExtractOutput, EntityDelta
from watson.memory.corrections import merge_entities
from watson.memory.db import connect, iso_now, parse_iso
from watson.memory.ids import new_id
from watson.memory.migrate import upgrade_head
from watson.pipeline.consolidation import (
    CONSOLIDATE_JOB_KIND,
    ConsolidationError,
    ConsolidationWait,
    _achievement_key,
    _fallback_summary,
    _grounded_for_kind,
    _insert_achievements,
    _insert_extract,
    _load_claims,
    _load_entities,
    _match_entity,
    _project_bucket,
    _record_understand_gaps,
    _summarize_day,
    _trim_numbered,
    _understand_jobs_for_day,
    catch_up_on_launch,
    consolidate_idempotency_key,
    consolidate_working_day,
    derived_counts,
    drain_consolidate_jobs,
    due_working_day_ids,
    enqueue_catch_up,
    ensure_consolidate_job,
    requeue_understand_jobs_for_day,
    retry_working_day,
    skipped_understand_segment_ids,
    understand_block_reason,
)
from watson.pipeline.frames import PIPELINE_VERSION
from watson.pipeline.jobs import DEFAULT_MAX_ATTEMPTS, JobState, backoff_seconds, enqueue
from watson.pipeline.segmentation import close_open_segments
from watson.privacy.audit import KIND_CONSOLIDATED
from watson.privacy.retention import Tier2SweepResult
from watson.privacy.spoolsweep import SpoolSweepResult

RECORDINGS = Path(__file__).resolve().parents[1] / "fixtures" / "runtime" / "consolidation.json"
NOW = datetime(2026, 10, 31, 20, 0, tzinfo=UTC)


def _write_achievements(
    conn: object, day_id: str, *, stamp: str, pipeline_version: int = PIPELINE_VERSION
) -> None:
    _insert_achievements(conn, day_id, pipeline_version=pipeline_version, stamp=stamp)


def _open(tmp_path: Path):
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    return connect(path, TEST_KEY, load_vec=False)


def _insert_day(conn: object, *, date: str = "2026-10-31", state: str = "open") -> str:
    day_id, stamp = new_id(), f"{date}T09:00:00Z"
    conn.execute(
        """
        INSERT INTO working_days (
            id, created_at, updated_at, date, started_at, ended_at,
            state, consolidation_error, pipeline_version
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, NULL, ?)
        """,
        (day_id, stamp, stamp, date, stamp, state, PIPELINE_VERSION),
    )
    conn.commit()
    return day_id


def _insert_frame(
    conn: object,
    day_id: str,
    *,
    state: str = "extracted",
    captured_at: str = "2026-10-31T09:00:00Z",
) -> str:
    frame_id = new_id()
    conn.execute(
        """
        INSERT INTO frames (
            id, created_at, updated_at, working_day_id, captured_at, bundle_id,
            app_name, window_title_hash, window_title, url_domain, phash, state,
            failure_count, deleted_at, deletion_verified
        ) VALUES (?, ?, ?, ?, ?, 'com.lumen.pages', 'Pages', NULL, 'Acme',
                  NULL, 'abcd', ?, 0, NULL, 0)
        """,
        (frame_id, captured_at, captured_at, day_id, captured_at, state),
    )
    if state in {"extracted", "deleted"}:
        conn.execute(
            """
            INSERT INTO frame_text (
                id, created_at, updated_at, frame_id, text_redacted,
                ocr_confidence, expires_at, source
            ) VALUES (?, ?, ?, ?, 'Still editing: Acme proposal appendix', 1.0, NULL, 'fixture')
            """,
            (new_id(), captured_at, captured_at, frame_id),
        )
    conn.commit()
    return frame_id


def _insert_segment(
    conn: object,
    day_id: str,
    frame_id: str,
    *,
    state: str = "closed",
    line_changes: int = 3,
    merged: str = "Still editing: Acme proposal appendix",
    started_at: str = "2026-10-31T09:00:00Z",
) -> str:
    segment_id = new_id()
    conn.execute(
        """
        INSERT INTO segments (
            id, created_at, updated_at, working_day_id, bundle_id, window_title_norm,
            url_domain, started_at, ended_at, frame_count, dwell_seconds, line_changes,
            merged_text, state, expires_at, pipeline_version, start_frame_id
        ) VALUES (?, ?, ?, ?, 'com.lumen.pages', 'acme', NULL, ?, ?, 1, 60, ?,
                  ?, ?, NULL, ?, ?)
        """,
        (
            segment_id,
            started_at,
            started_at,
            day_id,
            started_at,
            started_at,
            line_changes,
            merged,
            state,
            PIPELINE_VERSION,
            frame_id,
        ),
    )
    conn.commit()
    return segment_id


def _insert_observation(
    conn: object,
    segment_id: str,
    *,
    description: str = "Editing Acme",
    pipeline_version: int = PIPELINE_VERSION,
    project_candidates: list[str] | None = None,
    people_candidates: list[str] | None = None,
) -> str:
    obs_id, stamp = new_id(), "2026-10-31T10:00:00Z"
    payload: dict[str, object] = {}
    if project_candidates is not None:
        payload["project_candidates"] = project_candidates
    if people_candidates is not None:
        payload["people_candidates"] = people_candidates
    raw_json = "{}" if not payload else json.dumps(payload)
    conn.execute(
        """
        INSERT INTO observations (
            id, created_at, updated_at, segment_id, activity_type, description,
            confidence, model_id, prompt_version, pipeline_version, raw_json
        ) VALUES (?, ?, ?, ?, 'knowledge_work', ?, 0.8, 'fake', 'understanding/v1', ?, ?)
        """,
        (obs_id, stamp, stamp, segment_id, description, pipeline_version, raw_json),
    )
    conn.commit()
    return obs_id


def _insert_claim(
    conn: object,
    observation_id: str,
    day_id: str,
    segment_id: str,
    frame_id: str,
    *,
    kind: str,
    assertion: str,
    text: str,
    evidence: bool = True,
    pipeline_version: int = PIPELINE_VERSION,
    snippet: str = "Still editing: Acme proposal appendix",
    created_at: str = "2026-10-31T10:00:00Z",
) -> str:
    claim_id, stamp = new_id(), created_at
    conn.execute(
        """
        INSERT INTO claims (
            id, created_at, updated_at, observation_id, working_day_id, kind, text,
            assertion, confidence, status, due_hint, pipeline_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.8, NULL, NULL, ?)
        """,
        (claim_id, stamp, stamp, observation_id, day_id, kind, text, assertion, pipeline_version),
    )
    if evidence:
        conn.execute(
            """
            INSERT INTO claim_evidence (
                id, created_at, updated_at, claim_id, segment_id, frame_id,
                snippet, snippet_kind, line_start, line_end
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ocr', 1, 1)
            """,
            (new_id(), stamp, stamp, claim_id, segment_id, frame_id, snippet),
        )
    conn.commit()
    return claim_id


def _insert_entity(conn: object, *, name: str = "Acme Q4 Proposal", typ: str = "project") -> str:
    entity_id, stamp = new_id(), "2026-10-31T09:00:00Z"
    conn.execute(
        """
        INSERT INTO entities (
            id, created_at, updated_at, type, canonical_name, description,
            first_seen, last_seen, provisional, merged_into_id
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, 0, NULL)
        """,
        (entity_id, stamp, stamp, typ, name, stamp, stamp),
    )
    conn.commit()
    return entity_id


def _seed_day(conn: object, *, date: str = "2026-10-31") -> dict[str, str]:
    day_id = _insert_day(conn, date=date)
    frame_id = _insert_frame(conn, day_id)
    changed = _insert_segment(conn, day_id, frame_id, line_changes=3)
    static_frame = _insert_frame(conn, day_id, captured_at="2026-10-31T11:00:00Z")
    static = _insert_segment(
        conn,
        day_id,
        static_frame,
        line_changes=0,
        started_at="2026-10-31T11:00:00Z",
    )
    obs_changed = _insert_observation(conn, changed, description="Drafting the Acme appendix")
    obs_static = _insert_observation(conn, static, description="Re-reading the same page")
    # Sequential created_at so 1-based extract indexes in consolidation.json
    # (decision=5, commitment=6) stay stable under ORDER BY created_at, id.
    seq = {"n": 0}

    def _claim(
        observation_id: str,
        segment_id: str,
        frame: str,
        *,
        kind: str,
        assertion: str,
        text: str,
        evidence: bool = True,
    ) -> str:
        seq["n"] += 1
        return _insert_claim(
            conn,
            observation_id,
            day_id,
            segment_id,
            frame,
            kind=kind,
            assertion=assertion,
            text=text,
            evidence=evidence,
            created_at=f"2026-10-31T10:00:{seq['n']:02d}Z",
        )

    observed = _claim(
        obs_changed,
        changed,
        frame_id,
        kind="outcome",
        assertion="observed",
        text="Appendix drafted",
    )
    inferred = _claim(
        obs_changed,
        changed,
        frame_id,
        kind="outcome",
        assertion="inferred",
        text="Probably finished",
    )
    unconfirmed = _claim(
        obs_changed,
        changed,
        frame_id,
        kind="outcome",
        assertion="unconfirmed",
        text="Maybe shipped",
    )
    activity = _claim(
        obs_changed,
        changed,
        frame_id,
        kind="activity",
        assertion="observed",
        text="Editing",
    )
    _claim(
        obs_changed,
        changed,
        frame_id,
        kind="decision",
        assertion="observed",
        text="Ship the appendix on Friday",
    )
    _claim(
        obs_changed,
        changed,
        frame_id,
        kind="commitment",
        assertion="observed",
        text="Send Priya the draft",
    )
    observed_achievement = _claim(
        obs_changed,
        changed,
        frame_id,
        kind="achievement",
        assertion="observed",
        text="Closed the pricing thread",
    )
    no_change = _claim(
        obs_static,
        static,
        static_frame,
        kind="outcome",
        assertion="observed",
        text="No new lines",
    )
    no_evidence = _claim(
        obs_changed,
        changed,
        frame_id,
        kind="outcome",
        assertion="observed",
        text="Orphan outcome",
        evidence=False,
    )
    entity_id = _insert_entity(
        conn, name="Acme Q4 Proposal" if date == "2026-10-31" else f"Acme {date}"
    )
    return {
        "day_id": day_id,
        "observed": observed,
        "inferred": inferred,
        "unconfirmed": unconfirmed,
        "activity": activity,
        "observed_achievement": observed_achievement,
        "no_change": no_change,
        "no_evidence": no_evidence,
        "entity_id": entity_id,
        "changed": changed,
    }


def _runtime() -> FakeRuntime:
    return FakeRuntime(recordings=RECORDINGS)


def test_consolidate_twice_identical_row_counts(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        day_id = seeded["day_id"]
        runtime = _runtime()
        consolidate_working_day(conn, day_id, runtime=runtime, embedding=runtime, now=NOW)
        first = derived_counts(conn, day_id)
        assert first["activities"] == 1
        assert first["decisions"] == 1
        assert first["commitments"] == 1
        assert first["history"] == 1
        assert first["achievements"] == 2
        assert first["summaries"] == 1
        row = conn.execute(
            "SELECT state, consolidation_error FROM working_days WHERE id = ?", (day_id,)
        ).fetchone()
        assert row["state"] == "consolidated"
        assert row["consolidation_error"] is None
        audit = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_CONSOLIDATED,)
        ).fetchone()[0]
        assert audit == 1

        consolidate_working_day(conn, day_id, runtime=runtime, embedding=runtime, now=NOW)
        second = derived_counts(conn, day_id)
        assert second == first
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_CONSOLIDATED,)
            ).fetchone()[0]
            == 2
        )
        kinds = {
            str(r[0])
            for r in conn.execute(
                """
                SELECT c.kind FROM achievement_candidates a
                JOIN claims c ON c.id = a.claim_id
                """
            )
        }
        assert kinds == {"outcome", "achievement"}
        assertions = {
            str(r[0])
            for r in conn.execute(
                """
                SELECT c.assertion FROM achievement_candidates a
                JOIN claims c ON c.id = a.claim_id
                """
            )
        }
        assert assertions == {"observed"}
    finally:
        conn.close()


def test_no_achievements_from_inferred_or_unchanged(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        runtime = _runtime()
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        claim_ids = {str(r[0]) for r in conn.execute("SELECT claim_id FROM achievement_candidates")}
        assert seeded["observed"] in claim_ids
        assert seeded["observed_achievement"] in claim_ids
        assert seeded["inferred"] not in claim_ids
        assert seeded["unconfirmed"] not in claim_ids
        assert seeded["activity"] not in claim_ids
        assert seeded["no_change"] not in claim_ids
        assert seeded["no_evidence"] not in claim_ids
        assert len(claim_ids) == 2
    finally:
        conn.close()


def _four_same_achievements(conn: object, *, distinct: bool = True) -> dict[str, object]:
    day_id = _insert_day(conn)
    claim_ids: list[str] = []
    for index in range(4):
        captured = f"2026-10-31T09:{index:02d}:00Z"
        frame_id = _insert_frame(conn, day_id, captured_at=captured)
        segment_id = _insert_segment(conn, day_id, frame_id, started_at=captured, line_changes=3)
        observation_id = _insert_observation(
            conn, segment_id, description=f"Publishing onboarding section 3 ({index})"
        )
        claim_ids.append(
            _insert_claim(
                conn,
                observation_id,
                day_id,
                segment_id,
                frame_id,
                kind="outcome",
                assertion="observed",
                text="Onboarding Guide section 3 published.",
            )
        )
    other_id = None
    if distinct:
        captured = "2026-10-31T10:00:00Z"
        frame_id = _insert_frame(conn, day_id, captured_at=captured)
        segment_id = _insert_segment(conn, day_id, frame_id, started_at=captured)
        observation_id = _insert_observation(conn, segment_id, description="Pricing wrap-up")
        other_id = _insert_claim(
            conn,
            observation_id,
            day_id,
            segment_id,
            frame_id,
            kind="achievement",
            assertion="observed",
            text="Closed the pricing thread",
        )
    return {"day_id": day_id, "claim_ids": claim_ids, "other_id": other_id}


def _insert_eligible_achievement(
    conn: object,
    day_id: str,
    *,
    text: str,
    pipeline_version: int = PIPELINE_VERSION,
    project_candidates: list[str] | None = None,
    captured_at: str = "2026-10-31T09:00:00Z",
    snippet: str | None = None,
) -> str:
    frame_id = _insert_frame(conn, day_id, captured_at=captured_at)
    segment_id = _insert_segment(conn, day_id, frame_id, started_at=captured_at, line_changes=3)
    observation_id = _insert_observation(
        conn,
        segment_id,
        description=text,
        pipeline_version=pipeline_version,
        project_candidates=project_candidates,
    )
    return _insert_claim(
        conn,
        observation_id,
        day_id,
        segment_id,
        frame_id,
        kind="outcome",
        assertion="observed",
        text=text,
        pipeline_version=pipeline_version,
        snippet=snippet or text,
        created_at=captured_at,
    )


def _candidate_links(conn: object, candidate_id: str) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT claim_id FROM achievement_candidate_claims WHERE candidate_id = ?",
            (candidate_id,),
        )
    }


def test_achievement_key_folds_case_whitespace_and_punctuation() -> None:
    folded = "onboarding guide section 3 published"
    assert _achievement_key("Onboarding Guide section 3 published.") == folded
    assert _achievement_key("onboarding  guide  SECTION 3 published") == folded
    assert _achievement_key("Onboarding-Guide, section 3 published!") == folded
    assert _achievement_key("Onboarding Guide section 4 published.") == (
        "onboarding guide section 4 published"
    )
    assert _achievement_key("Onboarding Guide section 4 published.") != folded
    assert _achievement_key("") == ""
    assert _achievement_key("   ") == ""
    assert _achievement_key("!!!") == ""


def test_blank_normalized_achievement_key_is_skipped(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        blank = _insert_eligible_achievement(
            conn, day_id, text="???", captured_at="2026-10-31T09:00:00Z"
        )
        spaces = _insert_eligible_achievement(
            conn, day_id, text="   ---   ", captured_at="2026-10-31T09:01:00Z"
        )
        kept = _insert_eligible_achievement(
            conn,
            day_id,
            text="Section 3 published.",
            captured_at="2026-10-31T09:02:00Z",
        )
        _write_achievements(conn, day_id, stamp="2026-10-31T18:30:00Z")
        conn.commit()
        rows = conn.execute("SELECT claim_id, text FROM achievement_candidates").fetchall()
        assert len(rows) == 1
        assert str(rows[0]["claim_id"]) == kept
        assert str(rows[0]["text"]) == "Section 3 published."
        linked = {
            str(row[0]) for row in conn.execute("SELECT claim_id FROM achievement_candidate_claims")
        }
        assert linked == {kept}
        assert blank not in linked
        assert spaces not in linked
    finally:
        conn.close()


def test_folded_achievement_texts_merge_and_near_misses_stay_separate(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        variants = [
            "Onboarding Guide section 3 published.",
            "onboarding  guide  SECTION 3 published",
            "Onboarding-Guide, section 3 published!",
        ]
        merged_ids = [
            _insert_eligible_achievement(
                conn,
                day_id,
                text=text,
                captured_at=f"2026-10-31T09:{index:02d}:00Z",
                snippet=f"fold-evidence-{index}",
            )
            for index, text in enumerate(variants)
        ]
        near_miss = _insert_eligible_achievement(
            conn,
            day_id,
            text="Onboarding Guide section 4 published.",
            captured_at="2026-10-31T09:10:00Z",
            snippet="near-miss-section-4",
        )
        _write_achievements(conn, day_id, stamp="2026-10-31T18:30:00Z")
        conn.commit()
        rows = conn.execute(
            "SELECT id, text FROM achievement_candidates ORDER BY text, id"
        ).fetchall()
        assert len(rows) == 2
        by_section = {
            "3": [row for row in rows if "section 3" in str(row["text"]).casefold()],
            "4": [row for row in rows if "section 4" in str(row["text"]).casefold()],
        }
        assert len(by_section["3"]) == 1
        assert len(by_section["4"]) == 1
        assert _candidate_links(conn, str(by_section["3"][0]["id"])) == set(merged_ids)
        assert _candidate_links(conn, str(by_section["4"][0]["id"])) == {near_miss}
        reachable = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT e.snippet FROM achievement_candidate_claims l
                JOIN claim_evidence e ON e.claim_id = l.claim_id
                WHERE l.candidate_id = ?
                """,
                (str(by_section["3"][0]["id"]),),
            )
        }
        assert reachable == {f"fold-evidence-{index}" for index in range(3)}
    finally:
        conn.close()


def test_same_text_in_different_projects_does_not_merge(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        _insert_entity(conn, name="Acme Q4 Proposal")
        _insert_entity(conn, name="Onboarding Guide")
        text = "Section 3 published."
        acme = _insert_eligible_achievement(
            conn,
            day_id,
            text=text,
            project_candidates=["Acme Q4 Proposal"],
            captured_at="2026-10-31T09:00:00Z",
        )
        onboarding = _insert_eligible_achievement(
            conn,
            day_id,
            text=text,
            project_candidates=["Onboarding Guide"],
            captured_at="2026-10-31T09:01:00Z",
        )
        _write_achievements(conn, day_id, stamp="2026-10-31T18:30:00Z")
        conn.commit()
        rows = conn.execute("SELECT id FROM achievement_candidates ORDER BY id").fetchall()
        assert len(rows) == 2
        link_sets = {frozenset(_candidate_links(conn, str(row["id"]))) for row in rows}
        assert link_sets == {frozenset({acme}), frozenset({onboarding})}
    finally:
        conn.close()


def test_same_text_in_same_project_merges(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        _insert_entity(conn, name="Acme Q4 Proposal")
        text = "Section 3 published."
        first = _insert_eligible_achievement(
            conn,
            day_id,
            text=text,
            project_candidates=["Acme Q4 Proposal"],
            captured_at="2026-10-31T09:00:00Z",
        )
        second = _insert_eligible_achievement(
            conn,
            day_id,
            text=text,
            project_candidates=["Acme Q4 Proposal"],
            captured_at="2026-10-31T09:01:00Z",
        )
        _write_achievements(conn, day_id, stamp="2026-10-31T18:30:00Z")
        conn.commit()
        rows = conn.execute("SELECT id FROM achievement_candidates").fetchall()
        assert len(rows) == 1
        assert _candidate_links(conn, str(rows[0]["id"])) == {first, second}
    finally:
        conn.close()


def _null_and_resolved_same_text(conn: object, *, null_first: bool) -> tuple[str, str, str]:
    day_id = _insert_day(conn)
    _insert_entity(conn, name="Acme Q4 Proposal")
    text = "Section 3 published."
    null_at = "2026-10-31T09:00:00Z" if null_first else "2026-10-31T09:01:00Z"
    project_at = "2026-10-31T09:01:00Z" if null_first else "2026-10-31T09:00:00Z"
    null_id = _insert_eligible_achievement(conn, day_id, text=text, captured_at=null_at)
    project_id = _insert_eligible_achievement(
        conn,
        day_id,
        text=text,
        project_candidates=["Acme Q4 Proposal"],
        captured_at=project_at,
    )
    _write_achievements(conn, day_id, stamp="2026-10-31T18:30:00Z")
    conn.commit()
    return null_id, project_id, day_id


def _assert_separate_candidates(conn: object, *claim_ids: str) -> None:
    rows = conn.execute("SELECT id FROM achievement_candidates").fetchall()
    assert len(rows) == len(claim_ids)
    link_sets = {frozenset(_candidate_links(conn, str(row["id"]))) for row in rows}
    assert link_sets == {frozenset({claim_id}) for claim_id in claim_ids}


def test_null_then_resolved_project_same_text_does_not_merge(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        null_id, project_id, _ = _null_and_resolved_same_text(conn, null_first=True)
        _assert_separate_candidates(conn, null_id, project_id)
    finally:
        conn.close()


def test_resolved_then_null_project_same_text_does_not_merge(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        null_id, project_id, _ = _null_and_resolved_same_text(conn, null_first=False)
        _assert_separate_candidates(conn, null_id, project_id)
    finally:
        conn.close()


def test_unresolved_project_name_falls_back_to_null_bucket(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        _insert_entity(conn, name="Acme Q4 Proposal")
        text = "Section 3 published."
        unresolved = _insert_eligible_achievement(
            conn,
            day_id,
            text=text,
            project_candidates=["Ghost Project"],
            captured_at="2026-10-31T09:00:00Z",
        )
        null_bucket = _insert_eligible_achievement(
            conn, day_id, text=text, captured_at="2026-10-31T09:01:00Z"
        )
        other_unresolved = _insert_eligible_achievement(
            conn,
            day_id,
            text=text,
            project_candidates=["Also Missing"],
            captured_at="2026-10-31T09:02:00Z",
        )
        resolved = _insert_eligible_achievement(
            conn,
            day_id,
            text=text,
            project_candidates=["Acme Q4 Proposal"],
            captured_at="2026-10-31T09:03:00Z",
        )
        _write_achievements(conn, day_id, stamp="2026-10-31T18:30:00Z")
        conn.commit()
        rows = conn.execute("SELECT id FROM achievement_candidates").fetchall()
        assert len(rows) == 2
        link_sets = {frozenset(_candidate_links(conn, str(row["id"]))) for row in rows}
        assert link_sets == {
            frozenset({unresolved, null_bucket, other_unresolved}),
            frozenset({resolved}),
        }
    finally:
        conn.close()


def test_first_resolved_project_wins_not_the_last(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        _insert_entity(conn, name="Acme Q4 Proposal")
        _insert_entity(conn, name="Onboarding Guide")
        text = "Section 3 published."
        both = _insert_eligible_achievement(
            conn,
            day_id,
            text=text,
            project_candidates=["Acme Q4 Proposal", "Onboarding Guide"],
            captured_at="2026-10-31T09:00:00Z",
        )
        first_only = _insert_eligible_achievement(
            conn,
            day_id,
            text=text,
            project_candidates=["Acme Q4 Proposal"],
            captured_at="2026-10-31T09:01:00Z",
        )
        last_only = _insert_eligible_achievement(
            conn,
            day_id,
            text=text,
            project_candidates=["Onboarding Guide"],
            captured_at="2026-10-31T09:02:00Z",
        )
        _write_achievements(conn, day_id, stamp="2026-10-31T18:30:00Z")
        conn.commit()
        rows = conn.execute("SELECT id FROM achievement_candidates").fetchall()
        assert len(rows) == 2
        link_sets = {frozenset(_candidate_links(conn, str(row["id"]))) for row in rows}
        assert link_sets == {frozenset({both, first_only}), frozenset({last_only})}
    finally:
        conn.close()


def test_later_resolved_project_used_when_first_name_is_unresolved(
    tmp_path: Path,
) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        _insert_entity(conn, name="Acme Q4 Proposal")
        text = "Section 3 published."
        skipped_then_acme = _insert_eligible_achievement(
            conn,
            day_id,
            text=text,
            project_candidates=["Ghost Project", "Acme Q4 Proposal"],
            captured_at="2026-10-31T09:00:00Z",
        )
        acme_only = _insert_eligible_achievement(
            conn,
            day_id,
            text=text,
            project_candidates=["Acme Q4 Proposal"],
            captured_at="2026-10-31T09:01:00Z",
        )
        null_bucket = _insert_eligible_achievement(
            conn, day_id, text=text, captured_at="2026-10-31T09:02:00Z"
        )
        _write_achievements(conn, day_id, stamp="2026-10-31T18:30:00Z")
        conn.commit()
        rows = conn.execute("SELECT id FROM achievement_candidates").fetchall()
        assert len(rows) == 2
        link_sets = {frozenset(_candidate_links(conn, str(row["id"]))) for row in rows}
        assert link_sets == {
            frozenset({skipped_then_acme, acme_only}),
            frozenset({null_bucket}),
        }
    finally:
        conn.close()


def test_project_bucket_matches_alias(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        entity_id = _insert_entity(conn, name="Canonical Corp")
        conn.execute(
            """
            INSERT INTO entity_aliases (
                id, created_at, updated_at, entity_id, alias, source
            ) VALUES ('alias-acme', '2026-10-31T09:00:00Z', '2026-10-31T09:00:00Z',
                      ?, 'Acme', 'model')
            """,
            (entity_id,),
        )
        conn.commit()
        text = "Section 3 published."
        aliased = _insert_eligible_achievement(
            conn,
            day_id,
            text=text,
            project_candidates=["Acme"],
            captured_at="2026-10-31T09:00:00Z",
        )
        canonical = _insert_eligible_achievement(
            conn,
            day_id,
            text=text,
            project_candidates=["Canonical Corp"],
            captured_at="2026-10-31T09:01:00Z",
        )
        raw = str(
            conn.execute(
                "SELECT raw_json FROM observations WHERE id = "
                "(SELECT observation_id FROM claims WHERE id = ?)",
                (aliased,),
            ).fetchone()[0]
        )
        assert _project_bucket(conn, raw) == entity_id
        _write_achievements(conn, day_id, stamp="2026-10-31T18:30:00Z")
        conn.commit()
        rows = conn.execute("SELECT id FROM achievement_candidates").fetchall()
        assert len(rows) == 1
        assert _candidate_links(conn, str(rows[0]["id"])) == {aliased, canonical}
    finally:
        conn.close()


def test_project_bucket_malformed_json_is_null(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        _insert_entity(conn, name="Acme Q4 Proposal")
        text = "Section 3 published."
        malformed = _insert_eligible_achievement(
            conn, day_id, text=text, captured_at="2026-10-31T09:00:00Z"
        )
        conn.execute(
            "UPDATE observations SET raw_json = '{not json' WHERE id = "
            "(SELECT observation_id FROM claims WHERE id = ?)",
            (malformed,),
        )
        conn.commit()
        raw = str(
            conn.execute(
                "SELECT raw_json FROM observations WHERE id = "
                "(SELECT observation_id FROM claims WHERE id = ?)",
                (malformed,),
            ).fetchone()[0]
        )
        assert _project_bucket(conn, raw) is None
        assert _project_bucket(conn, "null") is None
        assert _project_bucket(conn, "[1, 2]") is None
        null_bucket = _insert_eligible_achievement(
            conn, day_id, text=text, captured_at="2026-10-31T09:01:00Z"
        )
        resolved = _insert_eligible_achievement(
            conn,
            day_id,
            text=text,
            project_candidates=["Acme Q4 Proposal"],
            captured_at="2026-10-31T09:02:00Z",
        )
        _write_achievements(conn, day_id, stamp="2026-10-31T18:30:00Z")
        conn.commit()
        rows = conn.execute("SELECT id FROM achievement_candidates").fetchall()
        assert len(rows) == 2
        link_sets = {frozenset(_candidate_links(conn, str(row["id"]))) for row in rows}
        assert link_sets == {frozenset({malformed, null_bucket}), frozenset({resolved})}
    finally:
        conn.close()


def test_merged_source_is_skipped_and_history_lands_on_dest(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        dest = _insert_entity(conn, name="Zebra Deal")
        source = _insert_entity(conn, name="Acme")
        conn.execute(
            """
            INSERT INTO entity_aliases (
                id, created_at, updated_at, entity_id, alias, source
            ) VALUES (?, '2026-10-31T09:00:00Z', '2026-10-31T09:00:00Z', ?, 'OldAcme', 'model')
            """,
            (new_id(), source),
        )
        conn.commit()
        merge_entities(conn, source, dest)
        names = {row["canonical_name"] for row in _load_entities(conn)}
        assert names == {"Zebra Deal"}
        assert _match_entity(conn, "Acme", "project") == dest
        assert _match_entity(conn, "OldAcme", "project") == dest
        assert _match_entity(conn, "Zebra Deal", "project") == dest

        day_id = _insert_day(conn)
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
            text="Appendix drafted",
        )
        evidence_before = int(conn.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0])
        _insert_extract(
            conn,
            day_id,
            DayExtractOutput(
                entity_deltas=[
                    EntityDelta(
                        entity_name="Acme",
                        entity_type="project",
                        delta_text="Moved the proposal forward.",
                        claim_indexes=[1],
                    )
                ],
                decisions=[],
                commitments=[],
            ),
            [{"id": claim_id}],
            prompt_version="day_extract/v1",
            pipeline_version=1,
            stamp="2026-10-31T18:30:00Z",
        )
        dest_hist = int(
            conn.execute(
                "SELECT COUNT(*) FROM entity_history WHERE entity_id = ?", (dest,)
            ).fetchone()[0]
        )
        src_hist = int(
            conn.execute(
                "SELECT COUNT(*) FROM entity_history WHERE entity_id = ?", (source,)
            ).fetchone()[0]
        )
        assert dest_hist == 1
        assert src_hist == 0
        assert (
            int(conn.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0])
            == evidence_before
        )
    finally:
        conn.close()


def test_project_bucket_first_resolved_else_null(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        acme = _insert_entity(conn, name="Acme Q4 Proposal")
        onboarding = _insert_entity(conn, name="Onboarding Guide")
        assert _project_bucket(conn, "{}") is None
        assert _project_bucket(conn, json.dumps({"project_candidates": []})) is None
        assert _project_bucket(conn, json.dumps({"project_candidates": ["Ghost Project"]})) is None
        assert (
            _project_bucket(
                conn,
                json.dumps({"project_candidates": ["Acme Q4 Proposal", "Onboarding Guide"]}),
            )
            == acme
        )
        assert (
            _project_bucket(
                conn,
                json.dumps({"project_candidates": ["Onboarding Guide", "Acme Q4 Proposal"]}),
            )
            == onboarding
        )
        assert (
            _project_bucket(
                conn,
                json.dumps({"project_candidates": ["Ghost Project", "Acme Q4 Proposal"]}),
            )
            == acme
        )
    finally:
        conn.close()


def test_achievements_do_not_merge_across_pipeline_versions(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        text = "Onboarding Guide section 3 published."
        v1 = _insert_eligible_achievement(
            conn, day_id, text=text, pipeline_version=1, captured_at="2026-10-31T09:00:00Z"
        )
        v2 = _insert_eligible_achievement(
            conn, day_id, text=text, pipeline_version=2, captured_at="2026-10-31T09:01:00Z"
        )
        _write_achievements(conn, day_id, pipeline_version=1, stamp="2026-10-31T18:30:00Z")
        conn.commit()
        v1_rows = conn.execute(
            "SELECT id, pipeline_version FROM achievement_candidates WHERE pipeline_version = 1"
        ).fetchall()
        assert len(v1_rows) == 1
        assert _candidate_links(conn, str(v1_rows[0]["id"])) == {v1}
        assert v2 not in _candidate_links(conn, str(v1_rows[0]["id"]))
        _write_achievements(conn, day_id, pipeline_version=2, stamp="2026-10-31T18:31:00Z")
        conn.commit()
        rows = conn.execute(
            """
            SELECT id, pipeline_version FROM achievement_candidates
            ORDER BY pipeline_version, id
            """
        ).fetchall()
        assert [int(row["pipeline_version"]) for row in rows] == [1, 2]
        by_version = {int(row["pipeline_version"]): str(row["id"]) for row in rows}
        assert _candidate_links(conn, by_version[1]) == {v1}
        assert _candidate_links(conn, by_version[2]) == {v2}
    finally:
        conn.close()


def test_achievements_do_not_merge_across_working_days(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_one = _insert_day(conn, date="2026-10-30")
        day_two = _insert_day(conn, date="2026-10-31")
        text = "Onboarding Guide section 3 published."
        first = _insert_eligible_achievement(
            conn, day_one, text=text, captured_at="2026-10-30T09:00:00Z"
        )
        second = _insert_eligible_achievement(
            conn, day_two, text=text, captured_at="2026-10-31T09:00:00Z"
        )
        _write_achievements(conn, day_one, stamp="2026-10-30T18:30:00Z")
        conn.commit()
        day_one_rows = conn.execute(
            "SELECT id FROM achievement_candidates WHERE working_day_id = ?",
            (day_one,),
        ).fetchall()
        assert len(day_one_rows) == 1
        assert _candidate_links(conn, str(day_one_rows[0]["id"])) == {first}
        assert second not in _candidate_links(conn, str(day_one_rows[0]["id"]))
        _write_achievements(conn, day_two, stamp="2026-10-31T18:30:00Z")
        conn.commit()
        rows = conn.execute(
            "SELECT id, working_day_id FROM achievement_candidates ORDER BY working_day_id, id"
        ).fetchall()
        assert len(rows) == 2
        by_day = {str(row["working_day_id"]): str(row["id"]) for row in rows}
        assert _candidate_links(conn, by_day[day_one]) == {first}
        assert _candidate_links(conn, by_day[day_two]) == {second}
    finally:
        conn.close()


def test_duplicate_achievements_collapse_to_one_candidate_with_all_links(
    tmp_path: Path,
) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _four_same_achievements(conn)
        _write_achievements(conn, str(seeded["day_id"]), stamp="2026-10-31T18:30:00Z")
        conn.commit()
        rows = conn.execute(
            "SELECT id, claim_id, text FROM achievement_candidates ORDER BY text, id"
        ).fetchall()
        assert len(rows) == 2
        published = [row for row in rows if "Onboarding" in str(row["text"])]
        pricing = [row for row in rows if "pricing" in str(row["text"]).casefold()]
        assert len(published) == 1
        assert len(pricing) == 1
        candidate_id = str(published[0]["id"])
        links = [
            str(row[0])
            for row in conn.execute(
                """
                SELECT claim_id FROM achievement_candidate_claims
                WHERE candidate_id = ? ORDER BY claim_id
                """,
                (candidate_id,),
            )
        ]
        assert set(links) == set(seeded["claim_ids"])
        assert len(links) == 4
        evidence = conn.execute(
            """
            SELECT COUNT(*) FROM achievement_candidate_claims l
            JOIN claim_evidence e ON e.claim_id = l.claim_id
            WHERE l.candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()[0]
        assert evidence == 4
        other_links = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT l.claim_id FROM achievement_candidate_claims l
                WHERE l.candidate_id = ?
                """,
                (str(pricing[0]["id"]),),
            )
        }
        assert other_links == {str(seeded["other_id"])}
    finally:
        conn.close()


def test_collapsed_achievements_are_idempotent_across_consolidation(
    tmp_path: Path,
) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _four_same_achievements(conn, distinct=False)
        runtime = _runtime()
        day_id = str(seeded["day_id"])
        consolidate_working_day(conn, day_id, runtime=runtime, embedding=runtime, now=NOW)
        first = derived_counts(conn, day_id)
        assert first["achievements"] == 1
        first_links = int(
            conn.execute("SELECT COUNT(*) FROM achievement_candidate_claims").fetchone()[0]
        )
        assert first_links == 4
        consolidate_working_day(conn, day_id, runtime=runtime, embedding=runtime, now=NOW)
        second = derived_counts(conn, day_id)
        assert second == first
        assert (
            int(conn.execute("SELECT COUNT(*) FROM achievement_candidate_claims").fetchone()[0])
            == 4
        )
        texts = [
            str(row[0])
            for row in conn.execute(
                "SELECT text FROM achievement_candidates WHERE working_day_id = ?",
                (day_id,),
            )
        ]
        assert texts == ["Onboarding Guide section 3 published."]
    finally:
        conn.close()


def test_failure_marks_failed_and_leaves_spool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _open(tmp_path)
    swept: list[str] = []

    def boom_tier2(*_a: object, **_k: object) -> Tier2SweepResult:
        swept.append("tier2")
        return Tier2SweepResult(0, 0, 0)

    def boom_spool(*_a: object, **_k: object) -> SpoolSweepResult:
        swept.append("spool")
        return SpoolSweepResult(0, 0, 0, 0, 0, 0)

    monkeypatch.setattr("watson.pipeline.consolidation.sweep_tier2", boom_tier2)
    monkeypatch.setattr("watson.pipeline.consolidation.sweep_spool", boom_spool)
    spool = tmp_path / "spool"
    spool.mkdir()
    marker = spool / "keep-me.png"
    marker.write_bytes(b"png")
    try:
        seeded = _seed_day(conn)
        runtime = FakeRuntime(complete_queue=[JobFailure("cluster boom"), JobFailure("retry")])
        with pytest.raises(JobFailure):
            consolidate_working_day(
                conn,
                seeded["day_id"],
                runtime=runtime,
                embedding=runtime,
                spool_dir=spool,
                now=NOW,
            )
        row = conn.execute(
            "SELECT state, consolidation_error FROM working_days WHERE id = ?",
            (seeded["day_id"],),
        ).fetchone()
        assert row["state"] == "failed"
        assert row["consolidation_error"] == "JobFailure"
        assert swept == []
        assert marker.is_file()
        assert derived_counts(conn, seeded["day_id"]) == {
            "activities": 0,
            "deals": 0,
            "decisions": 0,
            "commitments": 0,
            "achievements": 0,
            "history": 0,
            "summaries": 0,
        }
    finally:
        conn.close()


def test_success_sweeps_after_verify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _open(tmp_path)
    swept: list[str] = []

    def rec_tier2(*_a: object, **_k: object) -> Tier2SweepResult:
        swept.append("tier2")
        return Tier2SweepResult(0, 0, 0)

    def rec_spool(*_a: object, **_k: object) -> SpoolSweepResult:
        swept.append("spool")
        return SpoolSweepResult(0, 0, 0, 0, 0, 0)

    monkeypatch.setattr("watson.pipeline.consolidation.sweep_tier2", rec_tier2)
    monkeypatch.setattr("watson.pipeline.consolidation.sweep_spool", rec_spool)
    try:
        seeded = _seed_day(conn)
        runtime = _runtime()
        consolidate_working_day(
            conn,
            seeded["day_id"],
            runtime=runtime,
            embedding=runtime,
            spool_dir=tmp_path / "spool",
            now=NOW,
        )
        assert swept == ["tier2", "spool"]
        row = conn.execute(
            "SELECT state FROM working_days WHERE id = ?", (seeded["day_id"],)
        ).fetchone()
        assert row["state"] == "consolidated"
    finally:
        conn.close()


def test_captured_frames_wait_before_deadline(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        _insert_frame(conn, day_id, state="captured")
        runtime = _runtime()
        early = datetime(2026, 10, 31, 19, 0, tzinfo=UTC)
        with pytest.raises(ConsolidationWait):
            consolidate_working_day(
                conn,
                day_id,
                runtime=runtime,
                embedding=runtime,
                now=early,
                recovery_deadline_hours=2,
            )
        row = conn.execute(
            "SELECT state, consolidation_error, consolidation_gap FROM working_days WHERE id = ?",
            (day_id,),
        ).fetchone()
        assert row["state"] == "closing"
        assert row["consolidation_error"] == "ConsolidationWait"
        assert row["consolidation_gap"] is None
        assert derived_counts(conn, day_id)["activities"] == 0
    finally:
        conn.close()


def test_captured_frames_after_deadline_record_gap(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        extracted = _insert_frame(conn, day_id, state="extracted")
        _insert_frame(conn, day_id, state="captured", captured_at="2026-10-31T16:00:00Z")
        segment_id = _insert_segment(conn, day_id, extracted, state="open")
        _insert_observation(conn, segment_id)
        _insert_entity(conn)
        runtime = _runtime()
        late = datetime(2026, 10, 31, 21, 0, tzinfo=UTC)
        consolidate_working_day(
            conn,
            day_id,
            runtime=runtime,
            embedding=runtime,
            now=late,
            recovery_deadline_hours=2,
        )
        row = conn.execute(
            "SELECT state, consolidation_gap FROM working_days WHERE id = ?", (day_id,)
        ).fetchone()
        assert row["state"] == "consolidated"
        assert row["consolidation_gap"] == "captured=1"
        assert (
            conn.execute("SELECT state FROM segments WHERE id = ?", (segment_id,)).fetchone()[0]
            == "closed"
        )
    finally:
        conn.close()


def test_catch_up_on_launch(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        yesterday = _seed_day(conn)
        conn.execute(
            "UPDATE working_days SET date = '2026-10-30' WHERE id = ?",
            (yesterday["day_id"],),
        )
        today = _insert_day(conn, date="2026-10-31", state="open")
        conn.commit()
        morning = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)
        assert due_working_day_ids(conn, now=morning, day_end=DEFAULT_DAY_END) == [
            yesterday["day_id"]
        ]
        runtime = _runtime()
        completed = catch_up_on_launch(
            conn, runtime=runtime, embedding=runtime, spool_dir=tmp_path / "spool", now=morning
        )
        assert completed == 1
        row = conn.execute(
            "SELECT state FROM working_days WHERE id = ?", (yesterday["day_id"],)
        ).fetchone()
        assert row["state"] == "consolidated"
        today_row = conn.execute("SELECT state FROM working_days WHERE id = ?", (today,)).fetchone()
        assert today_row["state"] == "open"
        assert enqueue_catch_up(conn, now=morning) == 0
        assert (
            drain_consolidate_jobs(
                conn, runtime=runtime, embedding=runtime, spool_dir=tmp_path / "spool", now=morning
            )
            == 0
        )
    finally:
        conn.close()


def test_close_open_segments_force_closes(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        frame_id = _insert_frame(conn, day_id)
        segment_id = _insert_segment(conn, day_id, frame_id, state="open")
        assert close_open_segments(conn, working_day_id=day_id) == 1
        assert (
            conn.execute("SELECT state FROM segments WHERE id = ?", (segment_id,)).fetchone()[0]
            == "closed"
        )
        assert close_open_segments(conn, working_day_id=day_id) == 0
    finally:
        conn.close()


def test_replace_does_not_append_across_pipeline_versions(tmp_path: Path) -> None:
    assert consolidate_idempotency_key("d1", 1) != consolidate_idempotency_key("d1", 2)
    assert consolidate_idempotency_key("d1", 1).endswith(":1")
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        runtime = _runtime()
        consolidate_working_day(
            conn,
            seeded["day_id"],
            runtime=runtime,
            embedding=runtime,
            now=NOW,
            pipeline_version=PIPELINE_VERSION,
        )
        current = derived_counts(conn, seeded["day_id"], PIPELINE_VERSION)
        stamp = "2026-10-31T21:00:00Z"
        conn.execute(
            """
            INSERT INTO activities (
                id, created_at, updated_at, working_day_id, title, summary,
                observation_ids, prompt_version, pipeline_version
            ) VALUES (?, ?, ?, ?, 'v99 only', 'keep', '[]', 'clustering/v1', 99)
            """,
            (new_id(), stamp, stamp, seeded["day_id"]),
        )
        conn.commit()
        assert derived_counts(conn, seeded["day_id"], 99)["activities"] == 1
        consolidate_working_day(
            conn,
            seeded["day_id"],
            runtime=runtime,
            embedding=runtime,
            now=NOW,
            pipeline_version=PIPELINE_VERSION,
        )
        assert derived_counts(conn, seeded["day_id"], PIPELINE_VERSION) == current
        assert derived_counts(conn, seeded["day_id"], 99)["activities"] == 1
        assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == (
            current["activities"] + 1
        )
    finally:
        conn.close()


def _insert_activity(conn: object, day_id: str, *, title: str, pipeline_version: int) -> str:
    act_id, stamp = new_id(), "2026-10-31T12:00:00Z"
    conn.execute(
        """
        INSERT INTO activities (
            id, created_at, updated_at, working_day_id, title, summary,
            observation_ids, prompt_version, pipeline_version
        ) VALUES (?, ?, ?, ?, ?, 'seed', '[]', 'clustering/v1', ?)
        """,
        (act_id, stamp, stamp, day_id, title, pipeline_version),
    )
    conn.commit()
    return act_id


def test_pending_understanding_is_scoped_to_this_day(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        other = _insert_day(conn, date="2026-10-30")
        frame = _insert_frame(conn, other, captured_at="2026-10-30T09:00:00Z")
        _insert_segment(conn, other, frame, state="closed", started_at="2026-10-30T09:00:00Z")
        runtime = _runtime()
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        row = conn.execute(
            "SELECT state FROM working_days WHERE id = ?", (seeded["day_id"],)
        ).fetchone()
        assert row["state"] == "consolidated"
        other_row = conn.execute("SELECT state FROM working_days WHERE id = ?", (other,)).fetchone()
        assert other_row["state"] == "open"
    finally:
        conn.close()


def test_runtime_unavailable_holds_day_not_failed(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        runtime = FakeRuntime(complete_queue=[RuntimeUnavailable("model down")])
        with pytest.raises(RuntimeUnavailable):
            consolidate_working_day(
                conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW
            )
        row = conn.execute(
            "SELECT state, consolidation_error FROM working_days WHERE id = ?",
            (seeded["day_id"],),
        ).fetchone()
        assert row["state"] == "closing"
        assert row["consolidation_error"] == "RuntimeUnavailable"
        assert derived_counts(conn, seeded["day_id"])["activities"] == 0
        job = enqueue(
            conn,
            kind=CONSOLIDATE_JOB_KIND,
            payload={"working_day_id": seeded["day_id"], "pipeline_version": PIPELINE_VERSION},
            idempotency_key=consolidate_idempotency_key(seeded["day_id"], PIPELINE_VERSION),
            now=NOW,
        )
        held = FakeRuntime(complete_queue=[RuntimeUnavailable("still down")])
        assert (
            drain_consolidate_jobs(
                conn, runtime=held, embedding=held, spool_dir=tmp_path / "spool", now=NOW
            )
            == 0
        )
        again = conn.execute(
            "SELECT state, attempts, last_error FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()
        assert again["state"] == JobState.PENDING.value
        assert again["attempts"] == 0
        assert again["last_error"] == "RuntimeUnavailable"
        day = conn.execute(
            "SELECT state FROM working_days WHERE id = ?", (seeded["day_id"],)
        ).fetchone()
        assert day["state"] == "closing"
    finally:
        conn.close()


def test_drain_holds_consolidation_wait_without_burning_attempts(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        _insert_frame(conn, day_id, state="captured")
        early = datetime(2026, 10, 31, 19, 0, tzinfo=UTC)
        job = enqueue(
            conn,
            kind=CONSOLIDATE_JOB_KIND,
            payload={"working_day_id": day_id, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=consolidate_idempotency_key(day_id, PIPELINE_VERSION),
            now=early,
        )
        runtime = _runtime()
        assert (
            drain_consolidate_jobs(
                conn, runtime=runtime, embedding=runtime, spool_dir=tmp_path / "spool", now=early
            )
            == 0
        )
        row = conn.execute(
            "SELECT state, attempts, last_error FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()
        assert row["state"] == JobState.PENDING.value
        assert row["attempts"] == 0
        assert row["last_error"] == "ConsolidationWait"
        day = conn.execute(
            "SELECT state, consolidation_error FROM working_days WHERE id = ?", (day_id,)
        ).fetchone()
        assert day["state"] == "closing"
        assert day["consolidation_error"] == "ConsolidationWait"
    finally:
        conn.close()


def test_retry_requeues_dead_understand_job(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn, state="failed")
        conn.execute(
            "UPDATE working_days SET consolidation_error = 'pending understanding' WHERE id = ?",
            (day_id,),
        )
        frame_id = _insert_frame(conn, day_id)
        segment_id = _insert_segment(conn, day_id, frame_id, state="closed")
        _insert_entity(conn)
        job = enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": segment_id, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=f"understand:{segment_id}:{PIPELINE_VERSION}",
        )
        conn.execute(
            "UPDATE jobs SET state = ?, attempts = 5, last_error = ? WHERE id = ?",
            (JobState.FAILED.value, "StructuredOutputError", job.id),
        )
        conn.commit()
        understand = {
            "activity_type": "knowledge_work",
            "description": "Drafting the Acme appendix",
            "confidence": 0.8,
            "claims": [
                {
                    "kind": "activity",
                    "text": "Editing",
                    "assertion": "observed",
                    "evidence": [
                        {
                            "snippet": "Still editing: Acme proposal appendix",
                            "line_start": 1,
                            "line_end": 1,
                        }
                    ],
                }
            ],
            "project_candidates": [],
            "people_candidates": [],
        }
        runtime = FakeRuntime(
            recordings=RECORDINGS,
            complete_queue=[
                understand,
                {
                    "activities": [
                        {
                            "title": "Acme",
                            "summary": "editing",
                            "observation_indexes": [1],
                        }
                    ]
                },
                {"entity_deltas": [], "decisions": [], "commitments": []},
            ],
        )
        retry_working_day(conn, day_id, runtime=runtime, embedding=runtime, now=NOW)
        row = conn.execute(
            "SELECT state, consolidation_error FROM working_days WHERE id = ?", (day_id,)
        ).fetchone()
        assert row["state"] == "consolidated"
        assert row["consolidation_error"] is None
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
        refreshed = conn.execute(
            "SELECT state, attempts FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()
        assert refreshed["state"] == JobState.COMPLETED.value
    finally:
        conn.close()


def test_catch_up_requeues_dead_consolidate_job(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        conn.execute(
            """
            UPDATE working_days
            SET date = '2026-10-30', state = 'failed', consolidation_error = 'JobFailure'
            WHERE id = ?
            """,
            (seeded["day_id"],),
        )
        job = enqueue(
            conn,
            kind=CONSOLIDATE_JOB_KIND,
            payload={"working_day_id": seeded["day_id"], "pipeline_version": PIPELINE_VERSION},
            idempotency_key=consolidate_idempotency_key(seeded["day_id"], PIPELINE_VERSION),
        )
        conn.execute(
            "UPDATE jobs SET state = ?, attempts = 5, last_error = ? WHERE id = ?",
            (JobState.FAILED.value, "JobFailure", job.id),
        )
        conn.commit()
        morning = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)
        runtime = _runtime()
        assert (
            catch_up_on_launch(
                conn, runtime=runtime, embedding=runtime, spool_dir=tmp_path / "spool", now=morning
            )
            == 1
        )
        day = conn.execute(
            "SELECT state FROM working_days WHERE id = ?", (seeded["day_id"],)
        ).fetchone()
        assert day["state"] == "consolidated"
        refreshed = conn.execute("SELECT state FROM jobs WHERE id = ?", (job.id,)).fetchone()
        assert refreshed["state"] == JobState.COMPLETED.value
    finally:
        conn.close()


def test_mid_step_failure_rolls_back_previous_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        runtime = _runtime()
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        first = derived_counts(conn, seeded["day_id"])
        titles = [
            str(row[0]) for row in conn.execute("SELECT title FROM activities ORDER BY title, id")
        ]
        claim_ids = {
            str(row[0]) for row in conn.execute("SELECT claim_id FROM achievement_candidates")
        }

        def boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("mid insert")

        monkeypatch.setattr("watson.pipeline.consolidation._insert_achievements", boom)
        with pytest.raises(RuntimeError, match="mid insert"):
            consolidate_working_day(
                conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW
            )
        assert derived_counts(conn, seeded["day_id"]) == first
        assert [
            str(row[0]) for row in conn.execute("SELECT title FROM activities ORDER BY title, id")
        ] == titles
        assert {
            str(row[0]) for row in conn.execute("SELECT claim_id FROM achievement_candidates")
        } == claim_ids
        row = conn.execute(
            "SELECT state, consolidation_error FROM working_days WHERE id = ?",
            (seeded["day_id"],),
        ).fetchone()
        assert row["state"] == "failed"
        assert row["consolidation_error"] == "RuntimeError"
    finally:
        conn.close()


def test_verify_failure_skips_retention_and_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _open(tmp_path)
    order: list[str] = []

    def missing(_conn: object, _day_id: str) -> dict[str, str] | None:
        order.append("verify")
        return None

    def rec_tier2(*_a: object, **_k: object) -> Tier2SweepResult:
        order.append("tier2")
        return Tier2SweepResult(0, 0, 0)

    def rec_spool(*_a: object, **_k: object) -> SpoolSweepResult:
        order.append("spool")
        return SpoolSweepResult(0, 0, 0, 0, 0, 0)

    monkeypatch.setattr("watson.pipeline.consolidation._reread_day", missing)
    monkeypatch.setattr("watson.pipeline.consolidation.sweep_tier2", rec_tier2)
    monkeypatch.setattr("watson.pipeline.consolidation.sweep_spool", rec_spool)
    try:
        seeded = _seed_day(conn)
        conn.execute("UPDATE frame_text SET expires_at = '2026-10-01T00:00:00Z'")
        conn.commit()
        runtime = _runtime()
        with pytest.raises(ConsolidationError, match="missing after wal_checkpoint"):
            consolidate_working_day(
                conn,
                seeded["day_id"],
                runtime=runtime,
                embedding=runtime,
                spool_dir=tmp_path / "spool",
                now=NOW,
            )
        assert order == ["verify"]
        row = conn.execute(
            "SELECT state, consolidation_error FROM working_days WHERE id = ?",
            (seeded["day_id"],),
        ).fetchone()
        assert row["state"] == "failed"
        assert row["consolidation_error"] == "ConsolidationError"
        expires = {
            str(r[0]) for r in conn.execute("SELECT expires_at FROM frame_text") if r[0] is not None
        }
        assert expires == {"2026-10-01T00:00:00Z"}
    finally:
        conn.close()


def test_delete_and_retention_are_scoped_to_day_and_version(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        other = _insert_day(conn, date="2026-10-30")
        other_frame = _insert_frame(conn, other, captured_at="2026-10-30T09:00:00Z")
        other_activity = _insert_activity(conn, other, title="other-day", pipeline_version=1)
        v2_activity = _insert_activity(conn, seeded["day_id"], title="v2-keep", pipeline_version=99)
        conn.execute(
            """
            UPDATE frame_text SET expires_at = '2026-10-01T00:00:00Z'
            WHERE frame_id IN (SELECT id FROM frames WHERE working_day_id = ?)
            """,
            (seeded["day_id"],),
        )
        conn.execute(
            "UPDATE frame_text SET expires_at = '2026-12-01T00:00:00Z' WHERE frame_id = ?",
            (other_frame,),
        )
        conn.commit()
        runtime = _runtime()
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        assert (
            conn.execute("SELECT id FROM activities WHERE id = ?", (other_activity,)).fetchone()
            is not None
        )
        assert (
            conn.execute("SELECT title FROM activities WHERE id = ?", (v2_activity,)).fetchone()[0]
            == "v2-keep"
        )
        other_exp = conn.execute(
            "SELECT expires_at FROM frame_text WHERE frame_id = ?", (other_frame,)
        ).fetchone()[0]
        assert other_exp == "2026-12-01T00:00:00Z"
        this_exp = [
            str(row[0])
            for row in conn.execute(
                """
                SELECT t.expires_at FROM frame_text t
                JOIN frames f ON f.id = t.frame_id
                WHERE f.working_day_id = ?
                """,
                (seeded["day_id"],),
            )
        ]
        assert this_exp
        assert "2026-10-01T00:00:00Z" not in this_exp
    finally:
        conn.close()


def test_achievements_and_deltas_grounded_in_day_claims(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        v2_claim = _insert_claim(
            conn,
            conn.execute("SELECT id FROM observations ORDER BY created_at, id LIMIT 1").fetchone()[
                0
            ],
            seeded["day_id"],
            seeded["changed"],
            conn.execute(
                "SELECT id FROM frames WHERE working_day_id = ? ORDER BY captured_at LIMIT 1",
                (seeded["day_id"],),
            ).fetchone()[0],
            kind="outcome",
            assertion="observed",
            text="v2 only outcome",
            pipeline_version=99,
        )
        runtime = FakeRuntime(
            recordings=RECORDINGS,
            complete_queue=[
                {
                    "activities": [
                        {
                            "title": "Acme Q4 proposal",
                            "summary": "Editing",
                            "observation_indexes": [1],
                        }
                    ]
                },
                {
                    "entity_deltas": [
                        {
                            "entity_name": "Acme Q4 Proposal",
                            "entity_type": "project",
                            "delta_text": "ungrounded",
                            "claim_indexes": [99],
                        },
                        {
                            "entity_name": "Nobody",
                            "entity_type": "person",
                            "delta_text": "ghost",
                            "claim_indexes": [1],
                        },
                    ],
                    "decisions": [{"text": "ungrounded decision", "claim_indexes": [99]}],
                    "commitments": [{"text": "ungrounded commit", "claim_indexes": [0]}],
                },
            ],
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        counts = derived_counts(conn, seeded["day_id"], PIPELINE_VERSION)
        assert counts["decisions"] == 0
        assert counts["commitments"] == 0
        assert counts["history"] == 0
        claim_ids = {
            str(row[0]) for row in conn.execute("SELECT claim_id FROM achievement_candidates")
        }
        assert seeded["observed"] in claim_ids
        assert v2_claim not in claim_ids
        assert all(
            int(row[0]) == PIPELINE_VERSION
            for row in conn.execute("SELECT pipeline_version FROM achievement_candidates")
        )
    finally:
        conn.close()


def test_grounded_history_stores_day_claim_ids(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        runtime = _runtime()
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        row = conn.execute(
            "SELECT entity_id, claim_ids FROM entity_history WHERE working_day_id = ?",
            (seeded["day_id"],),
        ).fetchone()
        assert row is not None
        assert str(row["entity_id"]) == seeded["entity_id"]
        stored = set(json.loads(str(row["claim_ids"])))
        assert stored
        day_claims = {
            str(r[0])
            for r in conn.execute(
                "SELECT id FROM claims WHERE working_day_id = ? AND pipeline_version = ?",
                (seeded["day_id"], PIPELINE_VERSION),
            )
        }
        assert stored <= day_claims
    finally:
        conn.close()


def _closed_ununderstood(conn: object) -> tuple[str, str]:
    day_id = _insert_day(conn)
    frame_id = _insert_frame(conn, day_id)
    segment_id = _insert_segment(conn, day_id, frame_id, state="closed")
    return day_id, segment_id


def _understand_output() -> dict[str, object]:
    return {
        "activity_type": "knowledge_work",
        "description": "Drafting the Acme appendix",
        "confidence": 0.8,
        "claims": [
            {
                "kind": "activity",
                "text": "Editing",
                "assertion": "observed",
                "evidence": [
                    {
                        "snippet": "Still editing: Acme proposal appendix",
                        "line_start": 1,
                        "line_end": 1,
                    }
                ],
            }
        ],
        "project_candidates": [],
        "people_candidates": [],
    }


def test_runtime_down_during_understand_holds_without_consolidating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    swept: list[str] = []

    def rec_tier2(*_a: object, **_k: object) -> Tier2SweepResult:
        swept.append("tier2")
        return Tier2SweepResult(0, 0, 0)

    def rec_spool(*_a: object, **_k: object) -> SpoolSweepResult:
        swept.append("spool")
        return SpoolSweepResult(0, 0, 0, 0, 0, 0)

    monkeypatch.setattr("watson.pipeline.consolidation.sweep_tier2", rec_tier2)
    monkeypatch.setattr("watson.pipeline.consolidation.sweep_spool", rec_spool)
    conn = _open(tmp_path)
    try:
        day_id, _segment_id = _closed_ununderstood(conn)
        spool = tmp_path / "spool"
        spool.mkdir()
        marker = spool / "keep-me.png"
        marker.write_bytes(b"png")
        job = enqueue(
            conn,
            kind=CONSOLIDATE_JOB_KIND,
            payload={"working_day_id": day_id, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=consolidate_idempotency_key(day_id, PIPELINE_VERSION),
            now=NOW,
        )
        runtime = FakeRuntime(complete_queue=[RuntimeUnavailable("model down")])
        assert (
            drain_consolidate_jobs(
                conn,
                runtime=runtime,
                embedding=runtime,
                spool_dir=spool,
                now=NOW,
            )
            == 0
        )
        day = conn.execute(
            "SELECT state, consolidation_error FROM working_days WHERE id = ?", (day_id,)
        ).fetchone()
        assert day["state"] == "closing"
        assert day["consolidation_error"] == "ConsolidationWait"
        row = conn.execute(
            "SELECT state, attempts, last_error FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()
        assert row["state"] == JobState.PENDING.value
        assert row["attempts"] == 0
        assert row["last_error"] == "ConsolidationWait"
        assert derived_counts(conn, day_id) == {
            "activities": 0,
            "deals": 0,
            "decisions": 0,
            "commitments": 0,
            "achievements": 0,
            "history": 0,
            "summaries": 0,
        }
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
        assert swept == []
        assert marker.is_file()
    finally:
        conn.close()


def test_pending_understanding_for_this_day_blocks_consolidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("watson.pipeline.consolidation.drain_understand_jobs", lambda *_a, **_k: 0)
    conn = _open(tmp_path)
    try:
        day_id, segment_id = _closed_ununderstood(conn)
        runtime = _runtime()
        with pytest.raises(ConsolidationWait, match="understanding held"):
            consolidate_working_day(conn, day_id, runtime=runtime, embedding=runtime, now=NOW)
        day = conn.execute(
            "SELECT state, consolidation_error FROM working_days WHERE id = ?", (day_id,)
        ).fetchone()
        assert day["state"] == "closing"
        assert day["consolidation_error"] == "ConsolidationWait"
        assert derived_counts(conn, day_id)["activities"] == 0
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
        pending = conn.execute(
            "SELECT id FROM jobs WHERE kind = ? AND state = ?",
            (UNDERSTAND_JOB_KIND, JobState.PENDING.value),
        ).fetchone()
        assert pending is not None
        assert (
            conn.execute(
                "SELECT 1 FROM segments WHERE id = ? AND state = 'closed'", (segment_id,)
            ).fetchone()
            is not None
        )
    finally:
        conn.close()


def test_claimed_understand_jobs_hold_day_even_with_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SIGKILL window: claimed jobs with observations must not consolidate the day."""
    monkeypatch.setattr("watson.pipeline.consolidation.drain_understand_jobs", lambda *_a, **_k: 0)
    conn = _open(tmp_path)
    try:
        day_id, segment_id = _closed_ununderstood(conn)
        _insert_observation(conn, segment_id)
        job = enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": segment_id, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=f"understand:{segment_id}:{PIPELINE_VERSION}",
            now=NOW,
        )
        conn.execute(
            """
            UPDATE jobs
            SET state = ?, claimed_by = 'dead-worker', leased_until = ?
            WHERE id = ?
            """,
            (JobState.CLAIMED.value, iso_now(NOW + timedelta(hours=1)), job.id),
        )
        conn.commit()
        runtime = _runtime()
        with pytest.raises(ConsolidationWait, match="understanding held"):
            consolidate_working_day(conn, day_id, runtime=runtime, embedding=runtime, now=NOW)
        day = conn.execute(
            "SELECT state, consolidation_error, consolidated_at FROM working_days WHERE id = ?",
            (day_id,),
        ).fetchone()
        assert day["state"] == "closing"
        assert day["consolidation_error"] == "ConsolidationWait"
        assert day["consolidated_at"] is None
        held = conn.execute("SELECT state FROM jobs WHERE id = ?", (job.id,)).fetchone()
        assert held["state"] == JobState.CLAIMED.value
        assert derived_counts(conn, day_id)["activities"] == 0
    finally:
        conn.close()


def test_permanently_broken_understanding_consolidates_excluding_failed_segment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    swept: list[str] = []

    def rec_tier2(*_a: object, **_k: object) -> Tier2SweepResult:
        swept.append("tier2")
        return Tier2SweepResult(0, 0, 0)

    def rec_spool(*_a: object, **_k: object) -> SpoolSweepResult:
        swept.append("spool")
        return SpoolSweepResult(0, 0, 0, 0, 0, 0)

    monkeypatch.setattr("watson.pipeline.consolidation.sweep_tier2", rec_tier2)
    monkeypatch.setattr("watson.pipeline.consolidation.sweep_spool", rec_spool)
    conn = _open(tmp_path)
    try:
        day_id, segment_id = _closed_ununderstood(conn)
        understand = enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": segment_id, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=f"understand:{segment_id}:{PIPELINE_VERSION}",
            now=NOW,
        )
        conn.execute(
            "UPDATE jobs SET attempts = ?, last_error = ? WHERE id = ?",
            (DEFAULT_MAX_ATTEMPTS - 1, "StructuredOutputError", understand.id),
        )
        conn.commit()
        consolidate = enqueue(
            conn,
            kind=CONSOLIDATE_JOB_KIND,
            payload={"working_day_id": day_id, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=consolidate_idempotency_key(day_id, PIPELINE_VERSION),
            now=NOW,
        )
        bad = {"not": "valid understanding"}
        broken = FakeRuntime(complete_queue=[bad, bad])
        assert (
            drain_consolidate_jobs(
                conn,
                runtime=broken,
                embedding=broken,
                spool_dir=tmp_path / "spool",
                now=NOW,
            )
            == 1
        )
        assert broken.complete_calls >= 2
        day = conn.execute(
            "SELECT state, consolidation_error, consolidation_gap FROM working_days WHERE id = ?",
            (day_id,),
        ).fetchone()
        assert day["state"] == "consolidated"
        assert day["consolidation_error"] is None
        assert "understand_failed=" in str(day["consolidation_gap"] or "")
        dead = conn.execute(
            "SELECT state, attempts FROM jobs WHERE id = ?", (understand.id,)
        ).fetchone()
        assert dead["state"] == JobState.FAILED.value
        assert dead["attempts"] == DEFAULT_MAX_ATTEMPTS
        held = conn.execute(
            "SELECT state, attempts FROM jobs WHERE id = ?", (consolidate.id,)
        ).fetchone()
        assert held["state"] == JobState.COMPLETED.value
        assert derived_counts(conn, day_id)["activities"] == 0
        assert swept == ["tier2", "spool"]

        recovered = FakeRuntime(recordings=RECORDINGS, complete_queue=[_understand_output()])
        retry_working_day(
            conn,
            day_id,
            runtime=recovered,
            embedding=recovered,
            spool_dir=tmp_path / "spool",
            now=NOW,
        )
        retry_day = conn.execute(
            "SELECT state, consolidation_error FROM working_days WHERE id = ?", (day_id,)
        ).fetchone()
        assert retry_day["state"] == "consolidated"
        assert retry_day["consolidation_error"] is None
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    finally:
        conn.close()


def test_verify_count_mismatch_fails_without_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    swept: list[str] = []
    calls = {"n": 0}
    real = derived_counts

    def mismatch(
        conn: object, working_day_id: str, pipeline_version: int = PIPELINE_VERSION
    ) -> dict[str, int]:
        calls["n"] += 1
        seen = real(conn, working_day_id, pipeline_version)
        if calls["n"] == 2:
            return {key: value + 1 for key, value in seen.items()}
        return seen

    def rec_tier2(*_a: object, **_k: object) -> Tier2SweepResult:
        swept.append("tier2")
        return Tier2SweepResult(0, 0, 0)

    def rec_spool(*_a: object, **_k: object) -> SpoolSweepResult:
        swept.append("spool")
        return SpoolSweepResult(0, 0, 0, 0, 0, 0)

    monkeypatch.setattr("watson.pipeline.consolidation.derived_counts", mismatch)
    monkeypatch.setattr("watson.pipeline.consolidation.sweep_tier2", rec_tier2)
    monkeypatch.setattr("watson.pipeline.consolidation.sweep_spool", rec_spool)
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        runtime = _runtime()
        with pytest.raises(ConsolidationError, match="derived rows missing"):
            consolidate_working_day(
                conn,
                seeded["day_id"],
                runtime=runtime,
                embedding=runtime,
                spool_dir=tmp_path / "spool",
                now=NOW,
            )
        assert calls["n"] >= 2
        assert swept == []
        row = conn.execute(
            "SELECT state, consolidation_error FROM working_days WHERE id = ?",
            (seeded["day_id"],),
        ).fetchone()
        assert row["state"] == "failed"
        assert row["consolidation_error"] == "ConsolidationError"
    finally:
        conn.close()


def test_load_claims_tied_created_at_orders_kind_assertion_text(tmp_path: Path) -> None:
    """Equal created_at must not fall back to id (UUIDv7 random tail)."""
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        frame_id = _insert_frame(conn, day_id)
        segment_id = _insert_segment(conn, day_id, frame_id)
        obs_id = _insert_observation(conn, segment_id)
        stamp = "2026-10-31T15:00:00Z"
        # IDs sort the opposite of (kind, assertion, text).
        commitment_id = "00000000-0000-4000-8000-000000000001"
        unconfirmed_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        later_text_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        first_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        for claim_id, kind, assertion, text in (
            (commitment_id, "commitment", "observed", "zzz last by kind"),
            (unconfirmed_id, "activity", "unconfirmed", "mmm middle by assertion"),
            (later_text_id, "activity", "observed", "mmm later by text"),
            (first_id, "activity", "observed", "aaa first by text"),
        ):
            conn.execute(
                """
                INSERT INTO claims (
                    id, created_at, updated_at, observation_id, working_day_id,
                    kind, text, assertion, confidence, status, due_hint, pipeline_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.8, NULL, NULL, ?)
                """,
                (
                    claim_id,
                    stamp,
                    stamp,
                    obs_id,
                    day_id,
                    kind,
                    text,
                    assertion,
                    PIPELINE_VERSION,
                ),
            )
        conn.commit()
        loaded = _load_claims(conn, day_id, PIPELINE_VERSION)
        assert [row["id"] for row in loaded] == [
            first_id,
            later_text_id,
            unconfirmed_id,
            commitment_id,
        ]
        assert [row["kind"] for row in loaded] == [
            "activity",
            "activity",
            "activity",
            "commitment",
        ]
        assert [row["assertion"] for row in loaded] == [
            "observed",
            "observed",
            "unconfirmed",
            "observed",
        ]
        assert [row["text"] for row in loaded] == [
            "aaa first by text",
            "mmm later by text",
            "mmm middle by assertion",
            "zzz last by kind",
        ]
    finally:
        conn.close()


def test_load_claims_ignores_other_pipeline_version(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        v2_claim = _insert_claim(
            conn,
            conn.execute("SELECT id FROM observations ORDER BY created_at, id LIMIT 1").fetchone()[
                0
            ],
            seeded["day_id"],
            seeded["changed"],
            conn.execute(
                "SELECT id FROM frames WHERE working_day_id = ? ORDER BY captured_at LIMIT 1",
                (seeded["day_id"],),
            ).fetchone()[0],
            kind="decision",
            assertion="observed",
            text="v2-only claim must be ignored",
            pipeline_version=99,
        )
        conn.execute(
            "UPDATE claims SET created_at = '2026-10-31T10:00:01Z' WHERE id = ?",
            (v2_claim,),
        )
        conn.commit()
        v1_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM claims WHERE working_day_id = ? AND pipeline_version = ?",
                (seeded["day_id"], PIPELINE_VERSION),
            ).fetchone()[0]
        )
        runtime = FakeRuntime(
            recordings=RECORDINGS,
            complete_queue=[
                {
                    "activities": [
                        {
                            "title": "Acme Q4 proposal",
                            "summary": "Editing",
                            "observation_indexes": [1],
                        }
                    ]
                },
                {
                    "entity_deltas": [],
                    "decisions": [
                        {"text": "other-version decision", "claim_indexes": [v1_count + 1]}
                    ],
                    "commitments": [],
                },
            ],
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        counts = derived_counts(conn, seeded["day_id"], PIPELINE_VERSION)
        assert counts["decisions"] == 0
        stored = {
            claim_id
            for row in conn.execute("SELECT claim_ids FROM decisions")
            for claim_id in json.loads(str(row[0]))
        }
        assert v2_claim not in stored
    finally:
        conn.close()


def test_requeue_preserves_pending_understand_attempts(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id, segment_id = _closed_ununderstood(conn)
        job = enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": segment_id, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=f"understand:{segment_id}:{PIPELINE_VERSION}",
            now=NOW,
        )
        conn.execute(
            "UPDATE jobs SET attempts = 4, last_error = ? WHERE id = ?",
            ("StructuredOutputError", job.id),
        )
        conn.commit()
        assert requeue_understand_jobs_for_day(conn, day_id, now=NOW) == 0
        row = conn.execute(
            "SELECT state, attempts, last_error FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()
        assert row["state"] == JobState.PENDING.value
        assert row["attempts"] == 4
        assert row["last_error"] == "StructuredOutputError"
    finally:
        conn.close()


def test_requeue_leaves_failed_understand_jobs(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id, segment_id = _closed_ununderstood(conn)
        job = enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": segment_id, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=f"understand:{segment_id}:{PIPELINE_VERSION}",
            now=NOW,
        )
        conn.execute(
            "UPDATE jobs SET state = ?, attempts = ?, last_error = ? WHERE id = ?",
            (JobState.FAILED.value, DEFAULT_MAX_ATTEMPTS, "StructuredOutputError", job.id),
        )
        conn.commit()
        assert requeue_understand_jobs_for_day(conn, day_id, now=NOW) == 0
        row = conn.execute(
            "SELECT state, attempts, last_error FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()
        assert row["state"] == JobState.FAILED.value
        assert row["attempts"] == DEFAULT_MAX_ATTEMPTS
        assert row["last_error"] == "StructuredOutputError"
    finally:
        conn.close()


def test_requeue_does_not_reset_other_day_or_version_understand_jobs(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id, segment_id = _closed_ununderstood(conn)
        other = _insert_day(conn, date="2026-10-30")
        other_frame = _insert_frame(conn, other, captured_at="2026-10-30T09:00:00Z")
        other_segment = _insert_segment(conn, other, other_frame, started_at="2026-10-30T09:00:00Z")
        this_v1 = enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": segment_id, "pipeline_version": 1},
            idempotency_key=f"understand:{segment_id}:1",
            now=NOW,
        )
        other_v1 = enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": other_segment, "pipeline_version": 1},
            idempotency_key=f"understand:{other_segment}:1",
            now=NOW,
        )
        this_v2 = enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": segment_id, "pipeline_version": 2},
            idempotency_key=f"understand:{segment_id}:2",
            now=NOW,
        )
        conn.execute(
            "UPDATE jobs SET state = ?, attempts = 5, last_error = 'StructuredOutputError'",
            (JobState.FAILED.value,),
        )
        conn.commit()
        scoped = {job.id for job in _understand_jobs_for_day(conn, day_id, 1)}
        assert scoped == {this_v1.id}
        assert requeue_understand_jobs_for_day(conn, day_id, pipeline_version=1, now=NOW) == 0
        this_row = conn.execute(
            "SELECT state, attempts FROM jobs WHERE id = ?", (this_v1.id,)
        ).fetchone()
        assert this_row["state"] == JobState.FAILED.value
        assert this_row["attempts"] == 5
        other_row = conn.execute(
            "SELECT state, attempts FROM jobs WHERE id = ?", (other_v1.id,)
        ).fetchone()
        assert other_row["state"] == JobState.FAILED.value
        assert other_row["attempts"] == 5
        v2_row = conn.execute(
            "SELECT state, attempts FROM jobs WHERE id = ?", (this_v2.id,)
        ).fetchone()
        assert v2_row["state"] == JobState.FAILED.value
        assert v2_row["attempts"] == 5
    finally:
        conn.close()


def test_ensure_consolidate_job_leaves_claimed_job(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        job = enqueue(
            conn,
            kind=CONSOLIDATE_JOB_KIND,
            payload={"working_day_id": day_id, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=consolidate_idempotency_key(day_id, PIPELINE_VERSION),
            now=NOW,
        )
        lease = (NOW + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "UPDATE jobs SET state = ?, claimed_by = ?, leased_until = ? WHERE id = ?",
            (JobState.CLAIMED.value, "watson-test", lease, job.id),
        )
        conn.commit()
        assert ensure_consolidate_job(conn, day_id, now=NOW) is False
        row = conn.execute(
            "SELECT state, claimed_by, attempts FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()
        assert row["state"] == JobState.CLAIMED.value
        assert row["claimed_by"] == "watson-test"
        assert row["attempts"] == 0
    finally:
        conn.close()


def test_ensure_consolidate_job_leaves_consolidated_day(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        runtime = _runtime()
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        job = enqueue(
            conn,
            kind=CONSOLIDATE_JOB_KIND,
            payload={"working_day_id": seeded["day_id"], "pipeline_version": PIPELINE_VERSION},
            idempotency_key=consolidate_idempotency_key(seeded["day_id"], PIPELINE_VERSION),
            now=NOW,
        )
        conn.execute(
            "UPDATE jobs SET state = ? WHERE id = ?",
            (JobState.COMPLETED.value, job.id),
        )
        conn.commit()
        assert ensure_consolidate_job(conn, seeded["day_id"], now=NOW) is False
        row = conn.execute("SELECT state, attempts FROM jobs WHERE id = ?", (job.id,)).fetchone()
        assert row["state"] == JobState.COMPLETED.value
        assert row["attempts"] == 0
        day = conn.execute(
            "SELECT state FROM working_days WHERE id = ?", (seeded["day_id"],)
        ).fetchone()
        assert day["state"] == "consolidated"
    finally:
        conn.close()


def test_broken_day_fails_when_other_day_has_pending_segment(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_a, segment_a = _closed_ununderstood(conn)
        day_b = _insert_day(conn, date="2026-10-30")
        frame_b = _insert_frame(conn, day_b, captured_at="2026-10-30T09:00:00Z")
        _insert_segment(conn, day_b, frame_b, started_at="2026-10-30T09:00:00Z")
        understand = enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": segment_a, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=f"understand:{segment_a}:{PIPELINE_VERSION}",
            now=NOW,
        )
        conn.execute(
            "UPDATE jobs SET attempts = ?, last_error = ? WHERE id = ?",
            (DEFAULT_MAX_ATTEMPTS - 1, "StructuredOutputError", understand.id),
        )
        conn.commit()
        bad = {"not": "valid understanding"}
        runtime = FakeRuntime(complete_queue=[bad, bad], recordings=RECORDINGS)
        consolidate_working_day(conn, day_a, runtime=runtime, embedding=runtime, now=NOW)
        assert runtime.complete_calls >= 2
        day = conn.execute(
            "SELECT state, consolidation_error, consolidation_gap FROM working_days WHERE id = ?",
            (day_a,),
        ).fetchone()
        assert day["state"] == "consolidated"
        assert "understand_failed=" in str(day["consolidation_gap"] or "")
        dead = conn.execute(
            "SELECT state, attempts FROM jobs WHERE id = ?", (understand.id,)
        ).fetchone()
        assert dead["state"] == JobState.FAILED.value
        assert dead["attempts"] >= DEFAULT_MAX_ATTEMPTS
        other = conn.execute("SELECT state FROM working_days WHERE id = ?", (day_b,)).fetchone()
        assert other["state"] == "open"
        day_a_obs = conn.execute(
            """
            SELECT COUNT(*) FROM observations
            WHERE segment_id IN (SELECT id FROM segments WHERE working_day_id = ?)
            """,
            (day_a,),
        ).fetchone()[0]
        assert day_a_obs == 0
    finally:
        conn.close()


def _completed_understand(conn: object, segment_id: str):
    job = enqueue(
        conn,
        kind=UNDERSTAND_JOB_KIND,
        payload={"segment_id": segment_id, "pipeline_version": 1},
        idempotency_key=f"understand:{segment_id}:1",
        now=NOW,
    )
    conn.execute(
        "UPDATE jobs SET state = ?, attempts = 1 WHERE id = ?",
        (JobState.COMPLETED.value, job.id),
    )
    conn.commit()
    return job


def test_requeue_skips_completed_understand_with_same_version_observation(
    tmp_path: Path,
) -> None:
    conn = _open(tmp_path)
    try:
        day_id, segment_id = _closed_ununderstood(conn)
        job = _completed_understand(conn, segment_id)
        _insert_observation(conn, segment_id, pipeline_version=1)
        assert requeue_understand_jobs_for_day(conn, day_id, pipeline_version=1, now=NOW) == 0
        row = conn.execute("SELECT state, attempts FROM jobs WHERE id = ?", (job.id,)).fetchone()
        assert row["state"] == JobState.COMPLETED.value
        assert row["attempts"] == 1
    finally:
        conn.close()


def test_requeue_resets_completed_understand_without_observation(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id, segment_id = _closed_ununderstood(conn)
        job = _completed_understand(conn, segment_id)
        assert requeue_understand_jobs_for_day(conn, day_id, pipeline_version=1, now=NOW) == 1
        row = conn.execute(
            "SELECT state, attempts, last_error FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()
        assert row["state"] == JobState.PENDING.value
        assert row["attempts"] == 1
        assert row["last_error"] is None
    finally:
        conn.close()


def test_requeue_resets_completed_understand_with_other_version_observation(
    tmp_path: Path,
) -> None:
    conn = _open(tmp_path)
    try:
        day_id, segment_id = _closed_ununderstood(conn)
        job = _completed_understand(conn, segment_id)
        _insert_observation(conn, segment_id, pipeline_version=2)
        assert requeue_understand_jobs_for_day(conn, day_id, pipeline_version=1, now=NOW) == 1
        row = conn.execute("SELECT state, attempts FROM jobs WHERE id = ?", (job.id,)).fetchone()
        assert row["state"] == JobState.PENDING.value
        assert row["attempts"] == 1
    finally:
        conn.close()


@pytest.mark.timeout(20)
def test_poison_understand_job_stops_cycling_within_bounded_catch_up(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id, segment_id = _closed_ununderstood(conn)
        understand = enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": segment_id, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=f"understand:{segment_id}:{PIPELINE_VERSION}",
            now=NOW,
        )
        stamp = iso_now(NOW)
        conn.execute(
            """
            INSERT INTO observations (
                id, created_at, updated_at, segment_id, activity_type, description,
                confidence, model_id, prompt_version, pipeline_version, raw_json
            ) VALUES (?, ?, ?, ?, 'knowledge_work', 'poison', 0.1, 'fake',
                      'understanding/v2', ?, ?)
            """,
            (new_id(), stamp, stamp, segment_id, PIPELINE_VERSION, "{not-json"),
        )
        conn.execute(
            "UPDATE jobs SET state = ?, attempts = 1, last_error = ? WHERE id = ?",
            (JobState.FAILED.value, "JobFailure", understand.id),
        )
        conn.commit()
        enqueue(
            conn,
            kind=CONSOLIDATE_JOB_KIND,
            payload={"working_day_id": day_id, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=consolidate_idempotency_key(day_id, PIPELINE_VERSION),
            now=NOW,
        )
        spool = tmp_path / "spool"
        spool.mkdir()
        runtime = _runtime()
        for step in range(12):
            catch_up_on_launch(
                conn,
                runtime=runtime,
                embedding=runtime,
                spool_dir=spool,
                now=NOW + timedelta(minutes=10 * step),
            )
        dead = conn.execute(
            "SELECT state, attempts, last_error FROM jobs WHERE id = ?", (understand.id,)
        ).fetchone()
        assert dead["state"] == JobState.FAILED.value
        assert dead["attempts"] == 1
        assert dead["last_error"] == "JobFailure"
        day = conn.execute(
            "SELECT state, consolidation_gap FROM working_days WHERE id = ?",
            (day_id,),
        ).fetchone()
        assert day["state"] == "consolidated"
        assert "understand_failed=" in str(day["consolidation_gap"] or "")
        consolidate = conn.execute(
            """
            SELECT state, attempts FROM jobs
            WHERE kind = ? AND payload LIKE ?
            """,
            (CONSOLIDATE_JOB_KIND, f"%{day_id}%"),
        ).fetchone()
        assert consolidate is not None
        assert consolidate["state"] == JobState.COMPLETED.value
        assert not (consolidate["state"] == JobState.PENDING.value and consolidate["attempts"] == 0)
    finally:
        conn.close()


@pytest.mark.timeout(20)
def test_poison_understand_does_not_block_catch_up_consolidation(tmp_path: Path) -> None:
    """A terminal-failed understand job is skipped; catch-up consolidates the day."""
    conn = _open(tmp_path)
    try:
        day_id, segment_id = _closed_ununderstood(conn)
        understand = enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": segment_id, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=f"understand:{segment_id}:{PIPELINE_VERSION}",
            now=NOW,
        )
        stamp = iso_now(NOW)
        conn.execute(
            """
            INSERT INTO observations (
                id, created_at, updated_at, segment_id, activity_type, description,
                confidence, model_id, prompt_version, pipeline_version, raw_json
            ) VALUES (?, ?, ?, ?, 'knowledge_work', 'poison', 0.1, 'fake',
                      'understanding/v2', ?, ?)
            """,
            (new_id(), stamp, stamp, segment_id, PIPELINE_VERSION, "{not-json"),
        )
        conn.execute(
            "UPDATE jobs SET state = ?, attempts = 1, last_error = ? WHERE id = ?",
            (JobState.FAILED.value, "JobFailure", understand.id),
        )
        consolidate = enqueue(
            conn,
            kind=CONSOLIDATE_JOB_KIND,
            payload={"working_day_id": day_id, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=consolidate_idempotency_key(day_id, PIPELINE_VERSION),
            now=NOW,
        )
        conn.execute(
            "UPDATE jobs SET state = ?, attempts = ?, last_error = ? WHERE id = ?",
            (
                JobState.FAILED.value,
                DEFAULT_MAX_ATTEMPTS,
                "ConsolidationError",
                consolidate.id,
            ),
        )
        conn.commit()
        assert (
            ensure_consolidate_job(conn, day_id, pipeline_version=PIPELINE_VERSION, now=NOW) is True
        )
        spool = tmp_path / "spool"
        spool.mkdir()
        runtime = _runtime()
        for step in range(DEFAULT_MAX_ATTEMPTS + 3):
            catch_up_on_launch(
                conn,
                runtime=runtime,
                embedding=runtime,
                spool_dir=spool,
                now=NOW + timedelta(minutes=10 * step),
            )
        day = conn.execute(
            "SELECT state, consolidation_gap FROM working_days WHERE id = ?",
            (day_id,),
        ).fetchone()
        assert day["state"] == "consolidated"
        assert "understand_failed=" in str(day["consolidation_gap"] or "")
        dead = conn.execute(
            "SELECT state, attempts FROM jobs WHERE id = ?", (understand.id,)
        ).fetchone()
        assert dead["state"] == JobState.FAILED.value
        assert dead["attempts"] == 1
    finally:
        conn.close()


def test_expired_understand_lease_recovers_then_day_consolidates(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id, segment_id = _closed_ununderstood(conn)
        job = enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": segment_id, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=f"understand:{segment_id}:{PIPELINE_VERSION}",
            now=NOW,
        )
        expired = iso_now(NOW - timedelta(hours=2))
        conn.execute(
            """
            UPDATE jobs
            SET state = ?, claimed_by = 'dead-worker', leased_until = ?
            WHERE id = ?
            """,
            (JobState.CLAIMED.value, expired, job.id),
        )
        conn.commit()
        runtime = FakeRuntime(recordings=RECORDINGS, complete_queue=[_understand_output()])
        consolidate_working_day(conn, day_id, runtime=runtime, embedding=runtime, now=NOW)
        day = conn.execute(
            "SELECT state, consolidation_error FROM working_days WHERE id = ?", (day_id,)
        ).fetchone()
        assert day["state"] == "consolidated"
        assert day["consolidation_error"] is None
        refreshed = conn.execute("SELECT state FROM jobs WHERE id = ?", (job.id,)).fetchone()
        assert refreshed["state"] == JobState.COMPLETED.value
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.timeout(15)
def test_held_day_does_not_block_other_day_drain(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        held_id = _insert_day(conn, date="2026-10-31")
        _insert_frame(conn, held_id, state="captured")
        early = datetime(2026, 10, 31, 19, 0, tzinfo=UTC)
        held_job = enqueue(
            conn,
            kind=CONSOLIDATE_JOB_KIND,
            payload={"working_day_id": held_id, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=consolidate_idempotency_key(held_id, PIPELINE_VERSION),
            now=early,
        )
        ready = _seed_day(conn, date="2026-10-30")
        ready_job = enqueue(
            conn,
            kind=CONSOLIDATE_JOB_KIND,
            payload={"working_day_id": ready["day_id"], "pipeline_version": PIPELINE_VERSION},
            idempotency_key=consolidate_idempotency_key(ready["day_id"], PIPELINE_VERSION),
            now=early,
        )
        runtime = _runtime()
        completed = drain_consolidate_jobs(
            conn,
            runtime=runtime,
            embedding=runtime,
            spool_dir=tmp_path / "spool",
            now=early,
            limit=32,
        )
        assert completed >= 1
        ready_day = conn.execute(
            "SELECT state FROM working_days WHERE id = ?", (ready["day_id"],)
        ).fetchone()
        assert ready_day["state"] == "consolidated"
        ready_row = conn.execute("SELECT state FROM jobs WHERE id = ?", (ready_job.id,)).fetchone()
        assert ready_row["state"] == JobState.COMPLETED.value
        held_day = conn.execute(
            "SELECT state, consolidation_error FROM working_days WHERE id = ?", (held_id,)
        ).fetchone()
        assert held_day["state"] == "closing"
        assert held_day["consolidation_error"] == "ConsolidationWait"
        held_row = conn.execute(
            "SELECT state, attempts, last_error, run_after FROM jobs WHERE id = ?",
            (held_job.id,),
        ).fetchone()
        assert held_row["state"] == JobState.PENDING.value
        assert held_row["attempts"] == 0
        assert held_row["last_error"] == "ConsolidationWait"
        assert parse_iso(str(held_row["run_after"])) >= early + timedelta(
            seconds=backoff_seconds(1)
        )
    finally:
        conn.close()


class _PhaseSequenceSpy:
    """Drive the real consolidator and record complete/end_phase order."""

    def __init__(self, inner: FakeRuntime) -> None:
        self.inner = inner
        self.name = getattr(inner, "name", "spy")
        self.events: list[str] = []

    def complete(self, messages: object, schema: object, **kwargs: object) -> object:
        blob = "\n".join(item["content"] for item in messages)  # type: ignore[index]
        if "Cluster these observations" in blob:
            tag = "cluster"
        elif "Extract cross-segment" in blob:
            tag = "extract"
        elif "Write the daily narrative" in blob:
            tag = "summary"
        else:
            tag = "understand"
        self.events.append(f"complete:{tag}")
        return self.inner.complete(messages, schema, **kwargs)

    def embed(self, texts: object) -> object:
        self.events.append("embed")
        return self.inner.embed(texts)

    def end_phase(self) -> None:
        self.events.append("end_phase")
        self.inner.end_phase()


def test_consolidate_unloads_once_after_cluster_extract_and_summary(tmp_path: Path) -> None:
    """U9/U10: cluster+extract+summary share one chat load; unload only after the last LLM step."""
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        spy = _PhaseSequenceSpy(_runtime())
        consolidate_working_day(conn, seeded["day_id"], runtime=spy, embedding=spy, now=NOW)
        start = spy.events.index("complete:cluster")
        assert spy.events[start:] == [
            "complete:cluster",
            "complete:extract",
            "complete:summary",
            "end_phase",
        ]
    finally:
        conn.close()


def test_completed_understand_jobs_do_not_block_fragmented_day(tmp_path: Path) -> None:
    """day_fragmented_multi shape: every understand job completed, consolidation proceeds."""
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        for index in range(27):
            captured = f"2026-10-31T09:{index:02d}:00Z"
            frame_id = _insert_frame(conn, day_id, captured_at=captured)
            segment_id = _insert_segment(
                conn,
                day_id,
                frame_id,
                started_at=captured,
                merged=f"Fragment {index} still editing the same long window " + ("x" * 80),
            )
            _insert_observation(
                conn,
                segment_id,
                description=("Long observation of switching apps and projects. " * 12),
            )
            job = enqueue(
                conn,
                kind=UNDERSTAND_JOB_KIND,
                payload={"segment_id": segment_id, "pipeline_version": PIPELINE_VERSION},
                idempotency_key=f"understand:{segment_id}:{PIPELINE_VERSION}",
                now=NOW,
            )
            conn.execute(
                "UPDATE jobs SET state = ? WHERE id = ?",
                (JobState.COMPLETED.value, job.id),
            )
        conn.commit()
        assert (
            understand_block_reason(conn, day_id, pipeline_version=PIPELINE_VERSION, now=NOW)
            is None
        )

        class _FailCluster(FakeRuntime):
            def complete(self, messages, schema, **kwargs):  # type: ignore[no-untyped-def]
                props = schema.get("properties") if isinstance(schema, dict) else None
                if isinstance(props, dict) and "activities" in props:
                    from watson.intelligence.runtime.types import OutputParseError

                    raise OutputParseError("truncated clustering JSON")
                return super().complete(messages, schema, **kwargs)

        runtime = _FailCluster(recordings=RECORDINGS)
        consolidate_working_day(conn, day_id, runtime=runtime, embedding=runtime, now=NOW)
        day = conn.execute("SELECT state FROM working_days WHERE id = ?", (day_id,)).fetchone()
        assert day["state"] == "consolidated"
        assert skipped_understand_segment_ids(conn, day_id) == []
        activities = int(conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0])
        assert activities == 27
    finally:
        conn.close()


def test_failed_understand_is_flagged_not_silent(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id, segment_id = _closed_ununderstood(conn)
        good_frame = _insert_frame(conn, day_id, captured_at="2026-10-31T10:00:00Z")
        good_segment = _insert_segment(conn, day_id, good_frame, started_at="2026-10-31T10:00:00Z")
        _insert_observation(conn, good_segment, description="Editing Acme")
        good_job = enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": good_segment, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=f"understand:{good_segment}:{PIPELINE_VERSION}",
            now=NOW,
        )
        conn.execute(
            "UPDATE jobs SET state = ? WHERE id = ?",
            (JobState.COMPLETED.value, good_job.id),
        )
        failed = enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": segment_id, "pipeline_version": PIPELINE_VERSION},
            idempotency_key=f"understand:{segment_id}:{PIPELINE_VERSION}",
            now=NOW,
        )
        conn.execute(
            "UPDATE jobs SET state = ?, attempts = 5, last_error = ? WHERE id = ?",
            (JobState.FAILED.value, "OutputParseError: truncated JSON", failed.id),
        )
        conn.commit()
        runtime = _runtime()
        consolidate_working_day(conn, day_id, runtime=runtime, embedding=runtime, now=NOW)
        day = conn.execute(
            "SELECT state, consolidation_gap FROM working_days WHERE id = ?", (day_id,)
        ).fetchone()
        assert day["state"] == "consolidated"
        assert day["consolidation_gap"] == "understand_failed=1"
        assert skipped_understand_segment_ids(conn, day_id) == [segment_id]
        summary = conn.execute(
            "SELECT narrative_md FROM daily_summaries WHERE working_day_id = ?", (day_id,)
        ).fetchone()
        assert "excluded after understanding failed" in str(summary["narrative_md"])
    finally:
        conn.close()


def test_extract_drops_decisions_not_grounded_in_decision_claims(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        runtime = FakeRuntime(
            recordings={
                "complete": [
                    {
                        "match": "Cluster these observations",
                        "response": {"activities": []},
                    },
                    {
                        "match": "Extract cross-segment",
                        "response": {
                            "entity_deltas": [],
                            "decisions": [{"text": "hallucinated", "claim_indexes": [1]}],
                            "commitments": [{"text": "hallucinated commit", "claim_indexes": [1]}],
                        },
                    },
                    {
                        "match": "Write the daily narrative",
                        "response": {"paragraphs": []},
                    },
                ]
            }
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        counts = derived_counts(conn, seeded["day_id"])
        assert counts["decisions"] == 0
        assert counts["commitments"] == 0
    finally:
        conn.close()


def test_understand_gap_token_is_replaced_not_appended(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        _record_understand_gaps(conn, day_id, ["seg-a", "seg-b"], now=NOW)
        _record_understand_gaps(conn, day_id, ["seg-a", "seg-b"], now=NOW)
        gap = conn.execute(
            "SELECT consolidation_gap FROM working_days WHERE id = ?", (day_id,)
        ).fetchone()[0]
        assert gap == "understand_failed=2"
        _record_understand_gaps(conn, day_id, ["seg-a"], now=NOW)
        gap = conn.execute(
            "SELECT consolidation_gap FROM working_days WHERE id = ?", (day_id,)
        ).fetchone()[0]
        assert gap == "understand_failed=1"
        conn.execute(
            "UPDATE working_days SET consolidation_gap = ? WHERE id = ?",
            ("captured=1", day_id),
        )
        conn.commit()
        _record_understand_gaps(conn, day_id, ["seg-a"], now=NOW)
        gap = conn.execute(
            "SELECT consolidation_gap FROM working_days WHERE id = ?", (day_id,)
        ).fetchone()[0]
        assert gap == "captured=1,understand_failed=1"
    finally:
        conn.close()


def test_grounded_for_kind_filters_and_recovers_text_overlap() -> None:
    decision_id, outcome_id = "dec-1", "out-1"
    claims = [
        {"id": outcome_id, "kind": "outcome", "text": "Appendix drafted"},
        {"id": decision_id, "kind": "decision", "text": "Ship the appendix on Friday"},
    ]
    mixed = _grounded_for_kind([1, 2], claims, "decision", text="Ship the appendix on Friday")
    assert mixed == [decision_id]
    recovered = _grounded_for_kind([1], claims, "decision", text="Ship the appendix on Friday")
    assert recovered == [decision_id]
    hallucinated = _grounded_for_kind([1], claims, "decision", text="hallucinated")
    assert hallucinated == []


def test_trim_numbered_caps_item_and_list() -> None:
    items = ["word " * 40, "short", "never shown because the list is full"]
    out = _trim_numbered(items, item_max=12, list_max=20)
    assert len(out) <= 20
    assert "…" in out
    assert "never shown" not in out


def test_fallback_summary_cites_first_claim() -> None:
    empty = _fallback_summary([])
    assert empty.paragraphs == []
    out = _fallback_summary([{"id": "claim-1", "kind": "decision", "text": "Go"}])
    assert out.paragraphs[0].claim_ids == ["claim-1"]
    assert "[c:claim-1]" in out.paragraphs[0].text


def test_summarize_day_falls_back_when_model_returns_empty_paragraphs() -> None:
    runtime = FakeRuntime(complete_queue=[{"paragraphs": []}])
    claims = [{"id": "claim-1", "kind": "decision", "assertion": "observed", "text": "Go"}]
    out = _summarize_day(runtime, claims, "Write the daily narrative")
    assert out.paragraphs == []

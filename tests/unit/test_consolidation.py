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
from watson.memory.db import connect
from watson.memory.ids import new_id
from watson.memory.migrate import upgrade_head
from watson.pipeline.consolidation import (
    CONSOLIDATE_JOB_KIND,
    ConsolidationError,
    ConsolidationWait,
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
)
from watson.pipeline.jobs import DEFAULT_MAX_ATTEMPTS, JobState, enqueue
from watson.pipeline.segmentation import close_open_segments
from watson.privacy.audit import KIND_CONSOLIDATED
from watson.privacy.retention import Tier2SweepResult
from watson.privacy.spoolsweep import SpoolSweepResult

RECORDINGS = Path(__file__).resolve().parents[1] / "fixtures" / "runtime" / "consolidation.json"
NOW = datetime(2026, 10, 31, 20, 0, tzinfo=UTC)


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
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, NULL, 1)
        """,
        (day_id, stamp, stamp, date, stamp, state),
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
                  ?, ?, NULL, 1, ?)
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
    pipeline_version: int = 1,
) -> str:
    obs_id, stamp = new_id(), "2026-10-31T10:00:00Z"
    conn.execute(
        """
        INSERT INTO observations (
            id, created_at, updated_at, segment_id, activity_type, description,
            confidence, model_id, prompt_version, pipeline_version, raw_json
        ) VALUES (?, ?, ?, ?, 'knowledge_work', ?, 0.8, 'fake', 'understanding/v1', ?, '{}')
        """,
        (obs_id, stamp, stamp, segment_id, description, pipeline_version),
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
    pipeline_version: int = 1,
) -> str:
    claim_id, stamp = new_id(), "2026-10-31T10:00:00Z"
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
            ) VALUES (?, ?, ?, ?, ?, ?, 'Still editing: Acme proposal appendix', 'ocr', 1, 1)
            """,
            (new_id(), stamp, stamp, claim_id, segment_id, frame_id),
        )
    conn.commit()
    return claim_id


def _insert_entity(conn: object, *, name: str = "Acme Q4 Proposal") -> str:
    entity_id, stamp = new_id(), "2026-10-31T09:00:00Z"
    conn.execute(
        """
        INSERT INTO entities (
            id, created_at, updated_at, type, canonical_name, description,
            first_seen, last_seen, provisional, merged_into_id
        ) VALUES (?, ?, ?, 'project', ?, NULL, ?, ?, 0, NULL)
        """,
        (entity_id, stamp, stamp, name, stamp, stamp),
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
    observed = _insert_claim(
        conn,
        obs_changed,
        day_id,
        changed,
        frame_id,
        kind="outcome",
        assertion="observed",
        text="Appendix drafted",
    )
    inferred = _insert_claim(
        conn,
        obs_changed,
        day_id,
        changed,
        frame_id,
        kind="outcome",
        assertion="inferred",
        text="Probably finished",
    )
    unconfirmed = _insert_claim(
        conn,
        obs_changed,
        day_id,
        changed,
        frame_id,
        kind="outcome",
        assertion="unconfirmed",
        text="Maybe shipped",
    )
    activity = _insert_claim(
        conn,
        obs_changed,
        day_id,
        changed,
        frame_id,
        kind="activity",
        assertion="observed",
        text="Editing",
    )
    observed_achievement = _insert_claim(
        conn,
        obs_changed,
        day_id,
        changed,
        frame_id,
        kind="achievement",
        assertion="observed",
        text="Closed the pricing thread",
    )
    no_change = _insert_claim(
        conn,
        obs_static,
        day_id,
        static,
        static_frame,
        kind="outcome",
        assertion="observed",
        text="No new lines",
    )
    no_evidence = _insert_claim(
        conn,
        obs_changed,
        day_id,
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
            pipeline_version=1,
        )
        v1 = derived_counts(conn, seeded["day_id"], 1)
        stamp = "2026-10-31T21:00:00Z"
        conn.execute(
            """
            INSERT INTO activities (
                id, created_at, updated_at, working_day_id, title, summary,
                observation_ids, prompt_version, pipeline_version
            ) VALUES (?, ?, ?, ?, 'v2 only', 'keep', '[]', 'clustering/v1', 2)
            """,
            (new_id(), stamp, stamp, seeded["day_id"]),
        )
        conn.commit()
        assert derived_counts(conn, seeded["day_id"], 2)["activities"] == 1
        consolidate_working_day(
            conn,
            seeded["day_id"],
            runtime=runtime,
            embedding=runtime,
            now=NOW,
            pipeline_version=1,
        )
        assert derived_counts(conn, seeded["day_id"], 1) == v1
        assert derived_counts(conn, seeded["day_id"], 2)["activities"] == 1
        assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == (
            v1["activities"] + 1
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
            payload={"working_day_id": seeded["day_id"], "pipeline_version": 1},
            idempotency_key=consolidate_idempotency_key(seeded["day_id"], 1),
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
            payload={"working_day_id": day_id, "pipeline_version": 1},
            idempotency_key=consolidate_idempotency_key(day_id, 1),
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
            payload={"segment_id": segment_id, "pipeline_version": 1},
            idempotency_key=f"understand:{segment_id}:1",
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
            ]
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
            payload={"working_day_id": seeded["day_id"], "pipeline_version": 1},
            idempotency_key=consolidate_idempotency_key(seeded["day_id"], 1),
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
        v2_activity = _insert_activity(conn, seeded["day_id"], title="v2-keep", pipeline_version=2)
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
            pipeline_version=2,
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
            ]
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        counts = derived_counts(conn, seeded["day_id"], 1)
        assert counts["decisions"] == 0
        assert counts["commitments"] == 0
        assert counts["history"] == 0
        claim_ids = {
            str(row[0]) for row in conn.execute("SELECT claim_id FROM achievement_candidates")
        }
        assert seeded["observed"] in claim_ids
        assert v2_claim not in claim_ids
        assert all(
            int(row[0]) == 1
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
                "SELECT id FROM claims WHERE working_day_id = ? AND pipeline_version = 1",
                (seeded["day_id"],),
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
            payload={"working_day_id": day_id, "pipeline_version": 1},
            idempotency_key=consolidate_idempotency_key(day_id, 1),
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


def test_permanently_broken_understanding_fails_day_and_stays_retryable(
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
            payload={"segment_id": segment_id, "pipeline_version": 1},
            idempotency_key=f"understand:{segment_id}:1",
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
            payload={"working_day_id": day_id, "pipeline_version": 1},
            idempotency_key=consolidate_idempotency_key(day_id, 1),
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
            == 0
        )
        assert broken.complete_calls == 2
        day = conn.execute(
            "SELECT state, consolidation_error FROM working_days WHERE id = ?", (day_id,)
        ).fetchone()
        assert day["state"] == "failed"
        assert day["consolidation_error"] == "ConsolidationError"
        dead = conn.execute(
            "SELECT state, attempts FROM jobs WHERE id = ?", (understand.id,)
        ).fetchone()
        assert dead["state"] == JobState.FAILED.value
        assert dead["attempts"] == DEFAULT_MAX_ATTEMPTS
        held = conn.execute(
            "SELECT state, attempts FROM jobs WHERE id = ?", (consolidate.id,)
        ).fetchone()
        assert held["state"] != JobState.PENDING.value or held["attempts"] > 0
        assert derived_counts(conn, day_id)["activities"] == 0
        assert swept == []

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

    def mismatch(conn: object, working_day_id: str, pipeline_version: int = 1) -> dict[str, int]:
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
            pipeline_version=2,
        )
        conn.execute(
            "UPDATE claims SET created_at = '2026-10-31T10:00:01Z' WHERE id = ?",
            (v2_claim,),
        )
        conn.commit()
        v1_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM claims WHERE working_day_id = ? AND pipeline_version = 1",
                (seeded["day_id"],),
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
            ]
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        counts = derived_counts(conn, seeded["day_id"], 1)
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
            payload={"segment_id": segment_id, "pipeline_version": 1},
            idempotency_key=f"understand:{segment_id}:1",
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


def test_requeue_zeros_failed_understand_attempts(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id, segment_id = _closed_ununderstood(conn)
        job = enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": segment_id, "pipeline_version": 1},
            idempotency_key=f"understand:{segment_id}:1",
            now=NOW,
        )
        conn.execute(
            "UPDATE jobs SET state = ?, attempts = ?, last_error = ? WHERE id = ?",
            (JobState.FAILED.value, DEFAULT_MAX_ATTEMPTS, "StructuredOutputError", job.id),
        )
        conn.commit()
        assert requeue_understand_jobs_for_day(conn, day_id, now=NOW) == 1
        row = conn.execute(
            "SELECT state, attempts, last_error FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()
        assert row["state"] == JobState.PENDING.value
        assert row["attempts"] == 0
        assert row["last_error"] is None
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
        assert requeue_understand_jobs_for_day(conn, day_id, pipeline_version=1, now=NOW) == 1
        this_row = conn.execute(
            "SELECT state, attempts FROM jobs WHERE id = ?", (this_v1.id,)
        ).fetchone()
        assert this_row["state"] == JobState.PENDING.value
        assert this_row["attempts"] == 0
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
            payload={"working_day_id": day_id, "pipeline_version": 1},
            idempotency_key=consolidate_idempotency_key(day_id, 1),
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
            payload={"working_day_id": seeded["day_id"], "pipeline_version": 1},
            idempotency_key=consolidate_idempotency_key(seeded["day_id"], 1),
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
            payload={"segment_id": segment_a, "pipeline_version": 1},
            idempotency_key=f"understand:{segment_a}:1",
            now=NOW,
        )
        conn.execute(
            "UPDATE jobs SET attempts = ?, last_error = ? WHERE id = ?",
            (DEFAULT_MAX_ATTEMPTS - 1, "StructuredOutputError", understand.id),
        )
        conn.commit()
        bad = {"not": "valid understanding"}
        runtime = FakeRuntime(complete_queue=[bad, bad])
        with pytest.raises(ConsolidationError, match="pending understanding"):
            consolidate_working_day(conn, day_a, runtime=runtime, embedding=runtime, now=NOW)
        assert runtime.complete_calls == 2
        day = conn.execute(
            "SELECT state, consolidation_error FROM working_days WHERE id = ?", (day_a,)
        ).fetchone()
        assert day["state"] == "failed"
        assert day["consolidation_error"] == "ConsolidationError"
        dead = conn.execute(
            "SELECT state, attempts FROM jobs WHERE id = ?", (understand.id,)
        ).fetchone()
        assert dead["state"] == JobState.FAILED.value
        assert dead["attempts"] == DEFAULT_MAX_ATTEMPTS
        other = conn.execute("SELECT state FROM working_days WHERE id = ?", (day_b,)).fetchone()
        assert other["state"] == "open"
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
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
        assert row["attempts"] == 0
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
        assert row["attempts"] == 0
    finally:
        conn.close()

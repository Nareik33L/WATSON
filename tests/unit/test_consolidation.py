"""Day consolidation: idempotent replace, observed-only achievements, failure, catch-up."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.replay_support import TEST_KEY
from watson.config.schema import DEFAULT_DAY_END
from watson.intelligence.runtime import FakeRuntime, JobFailure
from watson.memory.db import connect
from watson.memory.ids import new_id
from watson.memory.migrate import upgrade_head
from watson.pipeline.consolidation import (
    ConsolidationWait,
    catch_up_on_launch,
    consolidate_idempotency_key,
    consolidate_working_day,
    derived_counts,
    drain_consolidate_jobs,
    due_working_day_ids,
    enqueue_catch_up,
)
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


def _insert_observation(conn: object, segment_id: str, *, description: str = "Editing Acme") -> str:
    obs_id, stamp = new_id(), "2026-10-31T10:00:00Z"
    conn.execute(
        """
        INSERT INTO observations (
            id, created_at, updated_at, segment_id, activity_type, description,
            confidence, model_id, prompt_version, pipeline_version, raw_json
        ) VALUES (?, ?, ?, ?, 'knowledge_work', ?, 0.8, 'fake', 'understanding/v1', 1, '{}')
        """,
        (obs_id, stamp, stamp, segment_id, description),
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
) -> str:
    claim_id, stamp = new_id(), "2026-10-31T10:00:00Z"
    conn.execute(
        """
        INSERT INTO claims (
            id, created_at, updated_at, observation_id, working_day_id, kind, text,
            assertion, confidence, status, due_hint, pipeline_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.8, NULL, NULL, 1)
        """,
        (claim_id, stamp, stamp, observation_id, day_id, kind, text, assertion),
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


def _seed_day(conn: object) -> dict[str, str]:
    day_id = _insert_day(conn)
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
    entity_id = _insert_entity(conn)
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
        assert row["consolidation_error"] is None
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

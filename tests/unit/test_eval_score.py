"""score_day / collect_predicted mutants: swap precision/recall, drop evidence."""

from __future__ import annotations

from pathlib import Path

from tests.replay_support import TEST_KEY
from watson.intelligence.eval.score import collect_predicted, score_day
from watson.memory.db import connect
from watson.memory.ids import new_id
from watson.memory.migrate import upgrade_head

TRUTH = {
    "projects": ["Acme Q4 Proposal", "Internal Onboarding Guide"],
    "people": ["Priya Natarajan (Acme)"],
    "decisions": [
        {"text": "Agreed to move the Acme demo to 14 Nov"},
        {"text": "Adopt the published three-tier rate card for Acme"},
    ],
    "commitments": [
        {"text": "Send revised pricing to Priya by Friday"},
        {"text": "Flag Q4 delivery risks in the proposal appendix"},
    ],
    "completed": ["Onboarding Guide section 3 published"],
    "not_completed": ["Acme proposal (still editing at day end)"],
    "noise": ["Cedar Daily"],
}


def _open(tmp_path: Path):
    path = tmp_path / "score.db"
    upgrade_head(path, TEST_KEY)
    path.chmod(0o600)
    return connect(path, TEST_KEY, load_vec=False)


def _seed(conn: object) -> str:
    day_id, stamp = new_id(), "2026-10-31T09:00:00Z"
    conn.execute(
        """
        INSERT INTO working_days (
            id, created_at, updated_at, date, started_at, ended_at,
            state, consolidation_error, pipeline_version
        ) VALUES (?, ?, ?, '2026-10-31', ?, NULL, 'consolidated', NULL, 1)
        """,
        (day_id, stamp, stamp, stamp),
    )
    frame_id = new_id()
    conn.execute(
        """
        INSERT INTO frames (
            id, created_at, updated_at, working_day_id, captured_at, bundle_id,
            app_name, window_title_hash, window_title, url_domain, phash, state,
            failure_count, deleted_at, deletion_verified
        ) VALUES (?, ?, ?, ?, ?, 'com.lumen.compass', 'Compass', NULL,
                  'Cedar Daily', NULL, 'abcd', 'extracted', 0, NULL, 0)
        """,
        (frame_id, stamp, stamp, day_id, stamp),
    )
    segment_id = new_id()
    conn.execute(
        """
        INSERT INTO segments (
            id, created_at, updated_at, working_day_id, bundle_id, window_title_norm,
            url_domain, started_at, ended_at, frame_count, dwell_seconds, line_changes,
            merged_text, state, expires_at, pipeline_version, start_frame_id
        ) VALUES (?, ?, ?, ?, 'com.lumen.compass', 'Cedar Daily', NULL, ?, ?, 1, 60, 1,
                  'Cedar Daily — Harbor ferry changes', 'closed', NULL, 1, ?)
        """,
        (segment_id, stamp, stamp, day_id, stamp, stamp, frame_id),
    )
    observation_id = new_id()
    conn.execute(
        """
        INSERT INTO observations (
            id, created_at, updated_at, segment_id, activity_type, description,
            confidence, model_id, prompt_version, pipeline_version, raw_json
        ) VALUES (?, ?, ?, ?, 'non_work', 'Cedar Daily glance', 0.9, 'replay',
                  'understanding/v1', 1, '{}')
        """,
        (observation_id, stamp, stamp, segment_id),
    )
    for typ, name in (
        ("project", "Acme Q4 Proposal"),
        ("project", "Internal Onboarding Guide"),
        ("person", "Priya Natarajan"),
    ):
        conn.execute(
            """
            INSERT INTO entities (
                id, created_at, updated_at, type, canonical_name, description,
                first_seen, last_seen, provisional, merged_into_id
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, 0, NULL)
            """,
            (new_id(), stamp, stamp, typ, name, stamp, stamp),
        )
    texts = [
        ("decision", "Agreed to move the Acme demo to 14 Nov"),
        ("decision", "Adopt the published three-tier rate card for Acme"),
        ("commitment", "Send revised pricing to Priya by Friday"),
        ("commitment", "Flag Q4 delivery risks in the proposal appendix"),
        ("achievement", "Onboarding Guide section 3 published"),
    ]
    first_claim = ""
    for kind, text in texts:
        claim_id = new_id()
        if not first_claim:
            first_claim = claim_id
        conn.execute(
            """
            INSERT INTO claims (
                id, created_at, updated_at, observation_id, working_day_id, kind, text,
                assertion, confidence, status, due_hint, pipeline_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'observed', 0.8, NULL, NULL, 1)
            """,
            (claim_id, stamp, stamp, observation_id, day_id, kind, text),
        )
        conn.execute(
            """
            INSERT INTO claim_evidence (
                id, created_at, updated_at, claim_id, segment_id, frame_id,
                snippet, snippet_kind, line_start, line_end
            ) VALUES (?, ?, ?, ?, ?, ?, 'quoted', 'ocr', 1, 1)
            """,
            (new_id(), stamp, stamp, claim_id, segment_id, frame_id),
        )
    conn.execute(
        """
        INSERT INTO daily_summaries (
            id, created_at, updated_at, working_day_id, narrative_md, sections_json,
            claim_ids, prompt_version, pipeline_version
        ) VALUES (?, ?, ?, ?, ?, '{}', '[]', 'daily_summary/v1', 1)
        """,
        (new_id(), stamp, stamp, day_id, f"Work continued [c:{first_claim}]."),
    )
    conn.commit()
    return day_id


def test_score_day_perfect_fixture(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        metrics = score_day(conn, _seed(conn), TRUTH)
        assert metrics["project_f1"] == 1.0
        assert metrics["people_f1"] == 1.0
        assert metrics["completed_accuracy"] == 1.0
        assert metrics["decisions_recall"] == 1.0
        assert metrics["decisions_precision"] == 1.0
        assert metrics["commitments_recall"] == 1.0
        assert metrics["commitments_precision"] == 1.0
        assert metrics["unsupported_achievements"] == 0.0
        assert metrics["duplicate_observation_rate"] == 0.0
        assert metrics["noise_non_work_rate"] == 1.0
        assert metrics["claims_with_evidence"] == 1.0
        assert metrics["citations_resolvable"] == 1.0
    finally:
        conn.close()


def test_score_day_does_not_swap_decision_precision_and_recall(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _seed(conn)
        conn.execute("DELETE FROM claims WHERE kind = 'decision' AND text LIKE '%rate card%'")
        conn.commit()
        metrics = score_day(conn, day_id, TRUTH)
        assert metrics["decisions_recall"] == 0.5
        assert metrics["decisions_precision"] == 1.0
        assert metrics["decisions_recall"] != metrics["decisions_precision"]
    finally:
        conn.close()


def test_noise_rate_ignores_cedar_daily_nav_in_merged_text(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _seed(conn)
        stamp = "2026-10-31T15:06:00Z"
        frame_id = new_id()
        conn.execute(
            """
            INSERT INTO frames (
                id, created_at, updated_at, working_day_id, captured_at, bundle_id,
                app_name, window_title_hash, window_title, url_domain, phash, state,
                failure_count, deleted_at, deletion_verified
            ) VALUES (?, ?, ?, ?, ?, 'com.lumen.compass', 'Compass', NULL,
                      'Onboarding Guide', NULL, 'abce', 'extracted', 0, NULL, 0)
            """,
            (frame_id, stamp, stamp, day_id, stamp),
        )
        segment_id = new_id()
        conn.execute(
            """
            INSERT INTO segments (
                id, created_at, updated_at, working_day_id, bundle_id, window_title_norm,
                url_domain, started_at, ended_at, frame_count, dwell_seconds, line_changes,
                merged_text, state, expires_at, pipeline_version, start_frame_id
            ) VALUES (?, ?, ?, ?, 'com.lumen.compass', 'Onboarding Guide — Section 3',
                      NULL, ?, ?, 1, 60, 1,
                      'Onboarding | Cedar Daily | Internal Onboarding Guide',
                      'closed', NULL, 1, ?)
            """,
            (segment_id, stamp, stamp, day_id, stamp, stamp, frame_id),
        )
        conn.execute(
            """
            INSERT INTO observations (
                id, created_at, updated_at, segment_id, activity_type, description,
                confidence, model_id, prompt_version, pipeline_version, raw_json
            ) VALUES (?, ?, ?, ?, 'knowledge_work', 'Onboarding Guide section 3',
                      0.9, 'replay', 'understanding/v1', 1, '{}')
            """,
            (new_id(), stamp, stamp, segment_id),
        )
        conn.commit()
        metrics = score_day(
            conn,
            day_id,
            {**TRUTH, "noise": ["Cedar Daily 10:40–10:46"]},
        )
        assert metrics["noise_non_work_rate"] == 1.0
    finally:
        conn.close()


def test_noise_rate_is_not_hardcoded_perfect(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _seed(conn)
        conn.execute("UPDATE observations SET activity_type = 'knowledge_work'")
        conn.commit()
        metrics = score_day(conn, day_id, TRUTH)
        assert metrics["noise_non_work_rate"] == 0.0
    finally:
        conn.close()


def test_empty_narrative_with_claims_is_not_resolvable(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _seed(conn)
        conn.execute(
            "UPDATE daily_summaries SET narrative_md = '' WHERE working_day_id = ?",
            (day_id,),
        )
        conn.commit()
        metrics = score_day(conn, day_id, TRUTH)
        predicted = collect_predicted(conn, day_id)
        assert predicted["citation_count"] == 0
        assert metrics["citations_resolvable"] == 0.0
    finally:
        conn.close()


def test_score_day_imperfect_values_for_each_metric(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _seed(conn)
        conn.execute("DELETE FROM entities WHERE type = 'project'")
        conn.execute("DELETE FROM claim_evidence")
        conn.execute(
            "UPDATE daily_summaries SET narrative_md = 'no citations' WHERE working_day_id = ?",
            (day_id,),
        )
        conn.commit()
        metrics = score_day(conn, day_id, TRUTH)
        assert metrics["project_f1"] < 1.0
        assert metrics["claims_with_evidence"] < 1.0
        assert metrics["citations_resolvable"] == 0.0
        assert metrics["unsupported_achievements"] == 0.0 or metrics["completed_accuracy"] <= 1.0
    finally:
        conn.close()


def test_score_day_duplicate_rate_ignores_same_description_revisits(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _seed(conn)
        stamp = "2026-10-31T10:00:00Z"
        frame_id = new_id()
        conn.execute(
            """
            INSERT INTO frames (
                id, created_at, updated_at, working_day_id, captured_at, bundle_id,
                app_name, window_title_hash, window_title, url_domain, phash, state,
                failure_count, deleted_at, deletion_verified
            ) VALUES (?, ?, ?, ?, ?, 'com.lumen.quill', 'Quill', NULL,
                      'Acme Q4 Proposal — Draft', NULL, 'ef01', 'extracted', 0, NULL, 0)
            """,
            (frame_id, stamp, stamp, day_id, stamp),
        )
        segment_id = new_id()
        conn.execute(
            """
            INSERT INTO segments (
                id, created_at, updated_at, working_day_id, bundle_id, window_title_norm,
                url_domain, started_at, ended_at, frame_count, dwell_seconds, line_changes,
                merged_text, state, expires_at, pipeline_version, start_frame_id
            ) VALUES (?, ?, ?, ?, 'com.lumen.quill', 'acme q4 proposal — draft', NULL,
                      ?, ?, 1, 60, 1, 'draft', 'closed', NULL, 1, ?)
            """,
            (segment_id, stamp, stamp, day_id, stamp, stamp, frame_id),
        )
        conn.execute(
            """
            INSERT INTO observations (
                id, created_at, updated_at, segment_id, activity_type, description,
                confidence, model_id, prompt_version, pipeline_version, raw_json
            ) VALUES (?, ?, ?, ?, 'knowledge_work', 'Cedar Daily glance', 0.9, 'replay',
                      'understanding/v1', 1, '{}')
            """,
            (new_id(), stamp, stamp, segment_id),
        )
        conn.commit()
        metrics = score_day(conn, day_id, TRUTH)
        # Same description on a later segment is a revisit, not a duplicate.
        assert metrics["duplicate_observation_rate"] == 0.0
    finally:
        conn.close()


def test_claims_with_evidence_is_not_count_of_claims(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        day_id = _seed(conn)
        conn.execute("DELETE FROM claim_evidence")
        conn.commit()
        metrics = score_day(conn, day_id, TRUTH)
        assert metrics["claims_with_evidence"] == 0.0
        predicted = collect_predicted(conn, day_id)
        assert predicted["claims_with_evidence"] == 0.0
    finally:
        conn.close()

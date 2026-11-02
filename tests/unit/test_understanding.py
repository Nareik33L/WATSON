"""Understanding worker: eligibility, persist, FakeRuntime acme_q4 replay."""

from __future__ import annotations

import hashlib
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlcipher3 import dbapi2 as sqlcipher

from tests.replay_support import DAY_ACME_Q4, TEST_KEY, open_test_db
from watson.config.schema import IntelligenceConfig
from watson.intelligence.eval.recordings import RecordingRuntime
from watson.intelligence.prompts import load_prompt
from watson.intelligence.resolver import resolve_candidates as resolve_candidates_impl
from watson.intelligence.runtime import UNDERSTAND_JOB_KIND, FakeRuntime, JobFailure
from watson.intelligence.runtime import http as http_mod
from watson.intelligence.runtime.stats import InferenceStats, TimingRuntime
from watson.intelligence.runtime.types import RuntimeUnavailable
from watson.intelligence.schemas.understanding import (
    ActivityType,
    Assertion,
    CandidateClaim,
    ClaimKind,
    EvidenceCitation,
    UnderstandingOutput,
)
from watson.memory.db import DatabaseError, connect
from watson.memory.ids import new_id
from watson.memory.migrate import upgrade_head
from watson.pipeline.backlog import drain_backlog
from watson.pipeline.consolidation import ConsolidationWait, consolidate_working_day, derived_counts
from watson.pipeline.frames import PIPELINE_VERSION
from watson.pipeline.jobs import (
    DEFAULT_LEASE_SECONDS,
    JobState,
    claim,
    enqueue,
    get_job,
    recover_expired_leases,
)
from watson.pipeline.replay import replay_fixture
from watson.pipeline.segmentation import close_open_segments
from watson.pipeline.understanding import (
    MERGED_TEXT_PROMPT_MAX,
    PROMPT_VERSION,
    UNDERSTAND_LEASE_SECONDS,
    UnderstandSettings,
    _citation_supported,
    _load_segment,
    _persist_observation,
    drain_understand_jobs,
    eligible_segment_ids,
    enqueue_eligible_understand_jobs,
    number_merged_text,
    understand_idempotency_key,
    understand_segment,
)
from watson.privacy.audit import KIND_CONSOLIDATED

ACME_RECORDINGS = Path(__file__).resolve().parents[1] / "fixtures" / "runtime" / "acme_q4.json"
CONSOLIDATION_RECORDINGS = (
    Path(__file__).resolve().parents[1] / "fixtures" / "runtime" / "consolidation.json"
)
_SIGKILL_WORKER = Path(__file__).resolve().parent / "sigkill_drain_worker.py"
_NOW = datetime(2026, 10, 31, 20, 0, tzinfo=UTC)


def _counts(conn: object) -> tuple[int, int, int, int, int]:
    return (
        int(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]),
        int(conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]),
        int(conn.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0]),
        int(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]),
        int(conn.execute("SELECT COUNT(*) FROM entity_aliases").fetchone()[0]),
    )


def _orphans(conn: object) -> list[str]:
    return [
        str(row[0])
        for row in conn.execute(
            """
            SELECT c.id FROM claims c
            LEFT JOIN claim_evidence e ON e.claim_id = c.id
            WHERE e.id IS NULL
            """
        )
    ]


_LINE = "Still editing: Acme proposal appendix"
_EMPTY_EVIDENCE_OUTPUT: dict[str, Any] = {
    "activity_type": "knowledge_work",
    "description": "claim without evidence",
    "confidence": 0.5,
    "claims": [
        {
            "kind": "activity",
            "text": "Editing",
            "assertion": "observed",
            "evidence": [],
        }
    ],
    "project_candidates": [],
    "people_candidates": [],
}


def _closed_segment(
    conn: object,
    *,
    merged: str = _LINE,
    day_id: str | None = None,
    started_at: str = "2026-10-31T09:00:00Z",
) -> str:
    frame_id, segment_id = new_id(), new_id()
    stamp = started_at
    if day_id is None:
        day_id = new_id()
        conn.execute(
            """
            INSERT INTO working_days (
                id, created_at, updated_at, date, started_at, ended_at,
                state, consolidation_error, pipeline_version
            ) VALUES (?, ?, ?, '2026-10-31', ?, NULL, 'open', NULL, 1)
            """,
            (day_id, stamp, stamp, stamp),
        )
    conn.execute(
        """
        INSERT INTO frames (
            id, created_at, updated_at, working_day_id, captured_at, bundle_id,
            app_name, window_title_hash, window_title, url_domain, phash, state,
            failure_count, deleted_at, deletion_verified
        ) VALUES (?, ?, ?, ?, ?, 'com.lumen.mail', 'Mail', NULL, 'Inbox',
                  NULL, 'abcd', 'extracted', 0, NULL, 0)
        """,
        (frame_id, stamp, stamp, day_id, stamp),
    )
    conn.execute(
        """
        INSERT INTO frame_text (
            id, created_at, updated_at, frame_id, text_redacted, ocr_confidence, expires_at, source
        ) VALUES (?, ?, ?, ?, ?, 1.0, NULL, 'fixture_sidecar')
        """,
        (new_id(), stamp, stamp, frame_id, merged),
    )
    conn.execute(
        """
        INSERT INTO segments (
            id, created_at, updated_at, working_day_id, bundle_id, window_title_norm,
            url_domain, started_at, ended_at, frame_count, dwell_seconds, line_changes,
            merged_text, state, expires_at, pipeline_version, start_frame_id
        ) VALUES (?, ?, ?, ?, 'com.lumen.mail', 'inbox', NULL, ?, ?, 1, 30, 0,
                  ?, 'closed', NULL, 1, ?)
        """,
        (segment_id, stamp, stamp, day_id, stamp, stamp, merged, frame_id),
    )
    conn.commit()
    return segment_id


def _output(*, evidence: list[EvidenceCitation]) -> UnderstandingOutput:
    return UnderstandingOutput.model_construct(
        activity_type=ActivityType.KNOWLEDGE_WORK,
        description="desc",
        confidence=0.5,
        claims=[
            CandidateClaim.model_construct(
                kind=ClaimKind.ACTIVITY,
                text="Editing",
                assertion=Assertion.OBSERVED,
                evidence=evidence,
            )
        ],
        project_candidates=[],
        people_candidates=[],
    )


def _seed_entity(conn: object, *, typ: str, name: str, alias: str | None = None) -> str:
    stamp, entity_id = "2026-10-31T09:00:00Z", new_id()
    conn.execute(
        """
        INSERT INTO entities (
            id, created_at, updated_at, type, canonical_name, description,
            first_seen, last_seen, provisional, merged_into_id
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, 0, NULL)
        """,
        (entity_id, stamp, stamp, typ, name, stamp, stamp),
    )
    if alias:
        conn.execute(
            """
            INSERT INTO entity_aliases (
                id, created_at, updated_at, entity_id, alias, source
            ) VALUES (?, ?, ?, ?, ?, 'seed')
            """,
            (new_id(), stamp, stamp, entity_id, alias),
        )
    conn.commit()
    return entity_id


def test_number_merged_text_and_eligibility_helpers() -> None:
    assert number_merged_text("one\n\ntwo", max_chars=32) == "1. one\n2. two"
    assert number_merged_text("", max_chars=8) == "(empty)"
    long = "\n".join(f"line-{index:04d} extra words" for index in range(1, 200))
    capped = number_merged_text(long, max_chars=4000)
    assert len(capped.replace("\n(truncated)", "")) <= 4000
    assert capped.endswith("(truncated)")
    assert MERGED_TEXT_PROMPT_MAX == 4000


def test_merged_text_prompt_max_keeps_4000_truncates_4001_and_empty() -> None:
    """C1 (>=), C2 (+1), C5 (first-line slice overflow), and empty."""
    assert number_merged_text("", max_chars=MERGED_TEXT_PROMPT_MAX) == "(empty)"
    exact = "x" * (MERGED_TEXT_PROMPT_MAX - 3)
    kept = number_merged_text(exact, max_chars=MERGED_TEXT_PROMPT_MAX)
    assert kept == f"1. {exact}"
    assert len(kept) == MERGED_TEXT_PROMPT_MAX
    assert "(truncated)" not in kept
    over = "x" * (MERGED_TEXT_PROMPT_MAX - 2)
    truncated = number_merged_text(over, max_chars=MERGED_TEXT_PROMPT_MAX)
    body = truncated.replace("\n(truncated)", "")
    assert truncated.endswith("(truncated)")
    assert len(body) == MERGED_TEXT_PROMPT_MAX
    assert body == f"1. {over}"[:MERGED_TEXT_PROMPT_MAX]


def test_merged_text_cap_counts_newlines_across_two_lines() -> None:
    """C1: ``>=`` vs ``>`` is visible when two numbered lines plus a newline hit 4000."""
    max_chars = MERGED_TEXT_PROMPT_MAX
    first = "a" * 2000
    second = "b" * 1993
    kept = number_merged_text(f"{first}\n{second}", max_chars=max_chars)
    expected = f"1. {first}\n2. {second}"
    assert kept == expected
    assert len(kept) == max_chars
    assert "\n" in kept
    assert "(truncated)" not in kept
    over = number_merged_text(f"{first}\n{second}x", max_chars=max_chars)
    assert over == f"1. {first}\n(truncated)"
    assert over.count("\n") == 1


def test_eligibility_closed_or_twenty_minutes(tmp_path: Path) -> None:
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        day_id = new_id()
        stamp = "2026-10-31T09:00:00Z"
        conn.execute(
            """
            INSERT INTO working_days (
                id, created_at, updated_at, date, started_at, ended_at,
                state, consolidation_error, pipeline_version
            ) VALUES (?, ?, ?, '2026-10-31', ?, NULL, 'open', NULL, 1)
            """,
            (day_id, stamp, stamp, stamp),
        )
        specs = (
            ("open-short", "open", 60),
            ("open-just-under", "open", 20 * 60 - 1),
            ("open-long", "open", 20 * 60),
            ("closed-short", "closed", 30),
        )
        ids: dict[str, str] = {}
        for name, state, dwell in specs:
            frame_id, segment_id = new_id(), new_id()
            ids[name] = segment_id
            conn.execute(
                """
                INSERT INTO frames (
                    id, created_at, updated_at, working_day_id, captured_at, bundle_id,
                    app_name, window_title_hash, window_title, url_domain, phash, state,
                    failure_count, deleted_at, deletion_verified
                ) VALUES (?, ?, ?, ?, ?, 'com.lumen.mail', 'Mail', NULL, 'Inbox',
                          NULL, 'abcd', 'extracted', 0, NULL, 0)
                """,
                (frame_id, stamp, stamp, day_id, stamp),
            )
            conn.execute(
                """
                INSERT INTO segments (
                    id, created_at, updated_at, working_day_id, bundle_id, window_title_norm,
                    url_domain, started_at, ended_at, frame_count, dwell_seconds, line_changes,
                    merged_text, state, expires_at, pipeline_version, start_frame_id
                ) VALUES (?, ?, ?, ?, 'com.lumen.mail', ?, NULL, ?, ?, 1, ?, 0,
                          'body', ?, NULL, 1, ?)
                """,
                (segment_id, stamp, stamp, day_id, name, stamp, stamp, dwell, state, frame_id),
            )
        conn.commit()
        eligible = set(eligible_segment_ids(conn))
        assert ids["open-short"] not in eligible
        assert ids["open-just-under"] not in eligible
        assert ids["open-long"] in eligible
        assert ids["closed-short"] in eligible
        added = enqueue_eligible_understand_jobs(conn)
        assert added == 2
        assert enqueue_eligible_understand_jobs(conn) == 0
    finally:
        conn.close()


def test_extract_drain_does_not_claim_understand_jobs(tmp_path: Path) -> None:
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        job = enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": "s1", "pipeline_version": 1},
            idempotency_key="understand:s1:1",
        )
        assert drain_backlog(conn, limit=4, fixture_dir=tmp_path) == 0
        assert get_job(conn, job.id).state is JobState.PENDING
        taken = claim(conn, worker_id="w", kind=UNDERSTAND_JOB_KIND)
        assert taken is not None and taken.id == job.id
    finally:
        conn.close()


def test_missing_runtime_leaves_understand_jobs_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        enqueue(
            conn,
            kind=UNDERSTAND_JOB_KIND,
            payload={"segment_id": "s1", "pipeline_version": 1},
            idempotency_key="understand:s1:1",
        )

        def boom(*_a: object, **_k: object) -> dict[str, Any]:
            raise RuntimeUnavailable("Ollama is not reachable at http://127.0.0.1:1")

        monkeypatch.setattr(http_mod, "request_json", boom)
        completed = drain_understand_jobs(
            conn, config=IntelligenceConfig(runtime="ollama", ollama_host="http://127.0.0.1:1")
        )
        assert completed == 0
        row = conn.execute("SELECT state, last_error, attempts FROM jobs").fetchone()
        assert row["state"] == JobState.PENDING.value
        assert row["attempts"] == 0
        assert "Ollama is not reachable" in str(row["last_error"])
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    finally:
        conn.close()


def test_acme_q4_fake_runtime_replay(data_dir: Path, key_provider: object, tmp_path: Path) -> None:
    conn = open_test_db(data_dir)
    try:
        result = replay_fixture(DAY_ACME_Q4, conn, instant=True, spool_dir=data_dir / "spool-acme")
        assert result.segments >= 1
        _seed_entity(conn, typ="project", name="Acme Q4 Proposal", alias="the Acme deal")
        _seed_entity(conn, typ="project", name="Internal Onboarding Guide")
        _seed_entity(conn, typ="person", name="Tom Ashworth")
        runtime = FakeRuntime(recordings=ACME_RECORDINGS)
        first = drain_understand_jobs(conn, runtime=runtime, embedding=runtime)
        assert first >= 1
        assert _orphans(conn) == []
        obs, claims, evidence, entities, aliases = _counts(conn)
        assert obs == first
        assert evidence >= claims
        assert claims == int(
            conn.execute("SELECT COUNT(DISTINCT claim_id) FROM claim_evidence").fetchone()[0]
        )
        versions = {
            (str(row[0]), int(row[1]))
            for row in conn.execute(
                "SELECT DISTINCT prompt_version, pipeline_version FROM observations"
            )
        }
        assert versions == {("understanding/v2", PIPELINE_VERSION)}

        acme = conn.execute(
            "SELECT id FROM entities WHERE canonical_name = ?", ("Acme Q4 Proposal",)
        ).fetchone()
        assert acme is not None
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM entities WHERE canonical_name = ?",
                ("Acme Q4 Proposal",),
            ).fetchone()[0]
            == 1
        )
        onboarding_aliases = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT a.alias FROM entity_aliases a
                JOIN entities e ON e.id = a.entity_id
                WHERE e.canonical_name = ?
                """,
                ("Internal Onboarding Guide",),
            )
        }
        assert "Onboarding Guide" in onboarding_aliases
        priya = conn.execute(
            """
            SELECT provisional FROM entities
            WHERE type = 'person' AND canonical_name = ?
            """,
            ("Priya Natarajan",),
        ).fetchone()
        assert priya is not None and int(priya[0]) == 1

        second = drain_understand_jobs(conn, runtime=runtime, embedding=runtime)
        assert second == 0
        assert _counts(conn) == (obs, claims, evidence, entities, aliases)
        assert _orphans(conn) == []
        pending = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE kind = ? AND state = ?",
            (UNDERSTAND_JOB_KIND, JobState.PENDING.value),
        ).fetchone()[0]
        assert pending == 0
    finally:
        conn.close()


def test_pipeline_version_is_part_of_idempotency_and_eligibility(tmp_path: Path) -> None:
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        segment_id = _closed_segment(conn)
        assert understand_idempotency_key(segment_id, 1) != understand_idempotency_key(
            segment_id, 2
        )
        assert understand_idempotency_key(segment_id, 1).endswith(":1")
        assert understand_idempotency_key(segment_id, 2).endswith(":2")

        assert enqueue_eligible_understand_jobs(conn, pipeline_version=1) == 1
        runtime = FakeRuntime()
        assert drain_understand_jobs(conn, runtime=runtime, embedding=runtime) == 1
        first = conn.execute(
            "SELECT id FROM observations WHERE segment_id = ? AND pipeline_version = 1",
            (segment_id,),
        ).fetchone()
        assert first is not None
        assert eligible_segment_ids(conn, pipeline_version=1) == []
        assert enqueue_eligible_understand_jobs(conn, pipeline_version=1) == 0
        again = understand_segment(
            conn, segment_id, runtime, runtime, settings=UnderstandSettings(pipeline_version=1)
        )
        assert again == str(first["id"])
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1

        assert eligible_segment_ids(conn, pipeline_version=2) == [segment_id]
        assert enqueue_eligible_understand_jobs(conn, pipeline_version=2) == 1
        assert drain_understand_jobs(conn, runtime=runtime, embedding=runtime) == 1
        versions = {
            int(row[0])
            for row in conn.execute(
                "SELECT pipeline_version FROM observations WHERE segment_id = ?",
                (segment_id,),
            )
        }
        assert versions == {1, 2}
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2
        assert enqueue_eligible_understand_jobs(conn, pipeline_version=2) == 0
        assert eligible_segment_ids(conn, pipeline_version=2) == []
    finally:
        conn.close()


def test_schema_and_persist_reject_claims_without_evidence(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="evidence"):
        CandidateClaim.model_validate(
            {"kind": "activity", "text": "Editing", "assertion": "observed", "evidence": []}
        )
    with pytest.raises(ValidationError, match="evidence"):
        UnderstandingOutput.model_validate(_EMPTY_EVIDENCE_OUTPUT)

    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        segment_id = _closed_segment(conn)
        enqueue_eligible_understand_jobs(conn)
        runtime = FakeRuntime(complete_queue=[_EMPTY_EVIDENCE_OUTPUT, _EMPTY_EVIDENCE_OUTPUT])
        assert drain_understand_jobs(conn, runtime=runtime, embedding=runtime) == 0
        assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
        failed = conn.execute("SELECT last_error, attempts FROM jobs").fetchone()
        assert failed["attempts"] >= 1
        assert "StructuredOutputError" in str(failed["last_error"])

        segment = _load_segment(conn, segment_id)
        assert segment is not None
        with pytest.raises(JobFailure, match="without evidence"):
            _persist_observation(
                conn,
                segment,
                _output(evidence=[]),
                prompt_version="understanding/v1",
                pipeline_version=1,
                model_id="fake",
                stamp="2026-10-31T10:00:00Z",
            )
        conn.rollback()
        assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0

        missing_line = EvidenceCitation.model_construct(snippet=_LINE, line_start=99, line_end=99)
        with pytest.raises(JobFailure, match="missing claim_evidence"):
            _persist_observation(
                conn,
                segment,
                _output(evidence=[missing_line]),
                prompt_version="understanding/v1",
                pipeline_version=1,
                model_id="fake",
                stamp="2026-10-31T10:00:00Z",
            )
        conn.rollback()
        assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0] == 0

        whitespace = EvidenceCitation.model_construct(snippet="   \n\t  ", line_start=1, line_end=1)
        assert _citation_supported(whitespace, [_LINE]) is False
        with pytest.raises(JobFailure, match="missing claim_evidence"):
            _persist_observation(
                conn,
                segment,
                _output(evidence=[whitespace]),
                prompt_version="understanding/v1",
                pipeline_version=1,
                model_id="fake",
                stamp="2026-10-31T10:00:00Z",
            )
        conn.rollback()
        assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0] == 0
    finally:
        conn.close()


def test_runtime_unavailable_mid_drain_holds_without_burning_attempts(tmp_path: Path) -> None:
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        _closed_segment(conn)
        enqueue_eligible_understand_jobs(conn)
        runtime = FakeRuntime(complete_queue=[RuntimeUnavailable("model vanished")])
        assert drain_understand_jobs(conn, runtime=runtime, embedding=runtime) == 0
        row = conn.execute("SELECT state, last_error, attempts FROM jobs").fetchone()
        assert row["state"] == JobState.PENDING.value
        assert row["attempts"] == 0
        assert row["last_error"] == "RuntimeUnavailable"
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    finally:
        conn.close()


UNDERSTANDING_V1_SHA256 = "49af407570f2d90a1d9fe055d14a283a12011b5d54495f2489fa6d60b904e781"


def test_understanding_v1_prompt_bytes_are_pinned() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "watson"
        / "intelligence"
        / "prompts"
        / "understanding"
        / "v1.md"
    )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == UNDERSTANDING_V1_SHA256
    prompt = load_prompt("understanding", "v1")
    assert prompt.prompt_version == "understanding/v1"
    assert "capped at 4000" not in prompt.text
    assert hashlib.sha256(prompt.text.encode("utf-8")).hexdigest() == UNDERSTANDING_V1_SHA256


def test_new_understand_run_records_v2_and_preserves_v1_rows(tmp_path: Path) -> None:
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        segment_id = _closed_segment(conn)
        segment = _load_segment(conn, segment_id)
        assert segment is not None
        v1_id = _persist_observation(
            conn,
            segment,
            UnderstandingOutput.model_validate(
                {
                    "activity_type": "knowledge_work",
                    "description": "v1 preserved description",
                    "confidence": 0.5,
                    "claims": [
                        {
                            "kind": "activity",
                            "text": "v1 preserved claim",
                            "assertion": "observed",
                            "evidence": [
                                {"snippet": "Still editing", "line_start": 1, "line_end": 1}
                            ],
                        }
                    ],
                    "project_candidates": [],
                    "people_candidates": [],
                }
            ),
            prompt_version="understanding/v1",
            pipeline_version=1,
            model_id="fake",
            stamp="2026-10-31T10:00:00Z",
        )
        conn.commit()
        runtime = FakeRuntime()
        v2_id = understand_segment(
            conn,
            segment_id,
            runtime,
            runtime,
            settings=UnderstandSettings(pipeline_version=PIPELINE_VERSION),
        )
        conn.commit()
        assert v2_id != v1_id
        assert PIPELINE_VERSION == 2
        assert PROMPT_VERSION == "v2"
        rows = {
            int(row["pipeline_version"]): (
                str(row["id"]),
                str(row["prompt_version"]),
                str(row["description"]),
            )
            for row in conn.execute(
                """
                SELECT id, prompt_version, pipeline_version, description
                FROM observations WHERE segment_id = ?
                """,
                (segment_id,),
            )
        }
        assert rows[1][0] == v1_id
        assert rows[1][1] == "understanding/v1"
        assert rows[1][2] == "v1 preserved description"
        assert rows[PIPELINE_VERSION][0] == v2_id
        assert rows[PIPELINE_VERSION][1] == "understanding/v2"
        v2_prompt = load_prompt("understanding", "v2")
        assert v2_prompt.prompt_version == "understanding/v2"
        assert "capped at 4000" in v2_prompt.text
    finally:
        conn.close()


_RESOLVE_OUTPUT: dict[str, Any] = {
    "activity_type": "knowledge_work",
    "description": "No completion evidence is visible.",
    "confidence": 0.7,
    "claims": [],
    "project_candidates": ["Harbor planning"],
    "people_candidates": ["Priya Natarajan"],
}


class _ResidentSpy:
    """Records the resident-model set at every complete/embed without auto-unloading."""

    def __init__(self, inner: FakeRuntime) -> None:
        self.inner = inner
        self.name = getattr(inner, "name", "spy")
        self.events: list[str] = []
        self.resident: set[str] = set()
        self.snapshots: list[frozenset[str]] = []
        self.complete_blobs: list[str] = []
        self.end_phase_count = 0
        self.complete_calls = 0

    def complete(self, messages: Any, schema: Any) -> dict[str, Any]:
        self.resident.add("chat")
        self.snapshots.append(frozenset(self.resident))
        self.events.append("complete")
        self.complete_calls += 1
        self.complete_blobs.append("\n".join(item["content"] for item in messages))
        return self.inner.complete(messages, schema)

    def embed(self, texts: Any) -> list[list[float]]:
        self.resident.add("embed")
        self.snapshots.append(frozenset(self.resident))
        self.events.append("embed")
        return self.inner.embed(texts)

    def end_phase(self) -> None:
        self.events.append("end_phase")
        self.end_phase_count += 1
        self.resident.clear()
        self.inner.end_phase()

    def probe(self) -> None:
        self.inner.probe()


def _entity_fingerprint(conn: object) -> set[tuple[str, str, str]]:
    return {
        (str(row[0]), str(row[1]), str(row[2]))
        for row in conn.execute(
            """
            SELECT e.type, e.canonical_name, a.alias
            FROM entities e
            JOIN entity_aliases a ON a.entity_id = e.id
            """
        )
    }


def test_drain_one_resident_model_and_single_chat_to_embed_switch(tmp_path: Path) -> None:
    """§11.4: chat-all, one unload, then deferred embeds. U8: drain finally end_phase."""
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        first = _closed_segment(conn, merged="alpha ocr still editing")
        day_id = str(
            conn.execute("SELECT working_day_id FROM segments WHERE id = ?", (first,)).fetchone()[0]
        )
        _closed_segment(
            conn,
            merged="beta ocr still editing",
            day_id=day_id,
            started_at="2026-10-31T10:00:00Z",
        )
        _seed_entity(conn, typ="project", name="Harbor Safety Audit")
        _seed_entity(conn, typ="person", name="Tom Ashworth")
        enqueue_eligible_understand_jobs(conn)
        inner = FakeRuntime(complete_queue=[dict(_RESOLVE_OUTPUT), dict(_RESOLVE_OUTPUT)])
        spy = _ResidentSpy(inner)
        assert drain_understand_jobs(conn, runtime=spy, embedding=spy) == 2
        assert spy.snapshots
        assert all(len(item) <= 1 for item in spy.snapshots)
        kinds = [event for event in spy.events if event in {"complete", "embed"}]
        assert kinds[0] == "complete"
        assert "embed" in kinds
        assert kinds[-1] == "embed"
        transitions = sum(1 for left, right in zip(kinds, kinds[1:], strict=False) if left != right)
        assert transitions == 1
        last_complete = max(i for i, event in enumerate(spy.events) if event == "complete")
        first_embed = spy.events.index("embed")
        assert last_complete < first_embed
        assert "end_phase" in spy.events[last_complete + 1 : first_embed]
        assert spy.events[-1] == "end_phase"
        assert spy.end_phase_count == 2
    finally:
        conn.close()


def test_drain_end_phase_forwards_through_timing_and_recording(tmp_path: Path) -> None:
    """U8, U11, U12: real drain → TimingRuntime → RecordingRuntime → FakeRuntime.end_phase."""
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        first = _closed_segment(conn)
        day_id = str(
            conn.execute("SELECT working_day_id FROM segments WHERE id = ?", (first,)).fetchone()[0]
        )
        _closed_segment(conn, day_id=day_id, started_at="2026-10-31T10:00:00Z")
        enqueue_eligible_understand_jobs(conn)
        inner = FakeRuntime()
        recording = RecordingRuntime(path=tmp_path / "rec.json", inner=inner, mode="record")
        wrapped = TimingRuntime(recording, InferenceStats())
        assert drain_understand_jobs(conn, runtime=wrapped, embedding=wrapped) == 2
        assert inner.end_phase_count == 2
    finally:
        conn.close()


def test_deferred_resolve_matches_interleaved_on_acme_q4(tmp_path: Path) -> None:
    def run(db_path: Path, *, deferred: bool) -> set[tuple[str, str, str]]:
        upgrade_head(db_path, TEST_KEY)
        conn = connect(db_path, TEST_KEY, load_vec=False)
        try:
            replay_fixture(
                DAY_ACME_Q4,
                conn,
                instant=True,
                spool_dir=db_path.parent / ("spool-d" if deferred else "spool-i"),
            )
            _seed_entity(conn, typ="project", name="Acme Q4 Proposal", alias="the Acme deal")
            _seed_entity(conn, typ="project", name="Internal Onboarding Guide")
            _seed_entity(conn, typ="person", name="Tom Ashworth")
            runtime = FakeRuntime(recordings=ACME_RECORDINGS)
            if deferred:
                drain_understand_jobs(conn, runtime=runtime, embedding=runtime)
            else:
                for segment_id in eligible_segment_ids(conn):
                    understand_segment(conn, segment_id, runtime, runtime)
            return _entity_fingerprint(conn)
        finally:
            conn.close()

    deferred = run(tmp_path / "deferred.db", deferred=True)
    interleaved = run(tmp_path / "interleaved.db", deferred=False)
    assert deferred == interleaved
    assert ("person", "Priya Natarajan", "Priya Natarajan") in deferred
    assert ("project", "Internal Onboarding Guide", "Onboarding Guide") in deferred


def _prepare_acme_q4(conn: object, spool: Path) -> None:
    replay_fixture(DAY_ACME_Q4, conn, instant=True, spool_dir=spool)
    _seed_entity(conn, typ="project", name="Acme Q4 Proposal", alias="the Acme deal")
    _seed_entity(conn, typ="project", name="Internal Onboarding Guide")
    _seed_entity(conn, typ="person", name="Tom Ashworth")


def _understand_job_states(conn: object) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT state FROM jobs WHERE kind = ?", (UNDERSTAND_JOB_KIND,))
    }


def _has_entity(conn: object, *, typ: str, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM entities WHERE type = ? AND canonical_name = ?",
        (typ, name),
    ).fetchone()
    return row is not None


def _has_alias(conn: object, *, canonical: str, alias: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM entity_aliases a
        JOIN entities e ON e.id = a.entity_id
        WHERE e.canonical_name = ? AND a.alias = ?
        """,
        (canonical, alias),
    ).fetchone()
    return row is not None


def test_crash_before_resolve_batch_reruns_from_raw_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crash after chat: jobs stay non-complete; rerun resolves from raw_json with 0 chats."""
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        _prepare_acme_q4(conn, tmp_path / "spool-crash")
        runtime = FakeRuntime(recordings=ACME_RECORDINGS)

        def crash(*_a: object, **_k: object) -> list[object]:
            raise RuntimeError("injected crash before resolve")

        monkeypatch.setattr("watson.pipeline.understanding.resolve_candidates", crash)
        with pytest.raises(RuntimeError, match="injected crash"):
            drain_understand_jobs(conn, runtime=runtime, embedding=runtime)
        assert JobState.COMPLETED.value not in _understand_job_states(conn)
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] >= 1
        assert not _has_entity(conn, typ="person", name="Priya Natarajan")
        assert not _has_alias(conn, canonical="Internal Onboarding Guide", alias="Onboarding Guide")
        chats = runtime.complete_calls
        assert chats >= 1
        monkeypatch.setattr(
            "watson.pipeline.understanding.resolve_candidates", resolve_candidates_impl
        )
        recovered = drain_understand_jobs(conn, runtime=runtime, embedding=runtime)
        assert recovered >= 1
        assert runtime.complete_calls == chats
        assert _has_entity(conn, typ="person", name="Priya Natarajan")
        assert _has_alias(conn, canonical="Internal Onboarding Guide", alias="Onboarding Guide")
        recovered_fp = _entity_fingerprint(conn)
    finally:
        conn.close()

    clean_path = tmp_path / "clean.db"
    upgrade_head(clean_path, TEST_KEY)
    clean = connect(clean_path, TEST_KEY, load_vec=False)
    try:
        _prepare_acme_q4(clean, tmp_path / "spool-clean")
        runtime = FakeRuntime(recordings=ACME_RECORDINGS)
        drain_understand_jobs(clean, runtime=runtime, embedding=runtime)
        assert _entity_fingerprint(clean) == recovered_fp
    finally:
        clean.close()


def test_database_locked_during_resolve_batch_reruns_from_raw_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        _prepare_acme_q4(conn, tmp_path / "spool-lock")
        runtime = FakeRuntime(recordings=ACME_RECORDINGS)

        def locked(*_a: object, **_k: object) -> list[object]:
            raise sqlcipher.DatabaseError("database is locked")

        monkeypatch.setattr("watson.pipeline.understanding.resolve_candidates", locked)
        with pytest.raises(sqlcipher.DatabaseError, match="database is locked"):
            drain_understand_jobs(conn, runtime=runtime, embedding=runtime)
        assert JobState.COMPLETED.value not in _understand_job_states(conn)
        assert not _has_entity(conn, typ="person", name="Priya Natarajan")
        chats = runtime.complete_calls
        monkeypatch.setattr(
            "watson.pipeline.understanding.resolve_candidates", resolve_candidates_impl
        )
        assert drain_understand_jobs(conn, runtime=runtime, embedding=runtime) >= 1
        assert runtime.complete_calls == chats
        assert _has_entity(conn, typ="person", name="Priya Natarajan")
        assert _has_alias(conn, canonical="Internal Onboarding Guide", alias="Onboarding Guide")
    finally:
        conn.close()


def test_resolve_batch_commit_visible_on_fresh_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D6: batch results are committed even if complete() then crashes."""
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        _prepare_acme_q4(conn, tmp_path / "spool-d6")
        runtime = FakeRuntime(recordings=ACME_RECORDINGS)

        def boom_complete(*_a: object, **_k: object) -> object:
            raise RuntimeError("crash after resolve commit")

        monkeypatch.setattr("watson.pipeline.understanding.complete", boom_complete)
        with pytest.raises(RuntimeError, match="crash after resolve commit"):
            drain_understand_jobs(conn, runtime=runtime, embedding=runtime)
        assert JobState.COMPLETED.value not in _understand_job_states(conn)
    finally:
        conn.close()
    fresh = connect(path, TEST_KEY, load_vec=False)
    try:
        assert _has_entity(fresh, typ="person", name="Priya Natarajan")
        assert _has_alias(fresh, canonical="Internal Onboarding Guide", alias="Onboarding Guide")
        assert fresh.execute("SELECT COUNT(*) FROM observations").fetchone()[0] >= 1
    finally:
        fresh.close()


def test_successful_drain_is_idempotent_and_creates_no_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        _prepare_acme_q4(conn, tmp_path / "spool-idemp")
        runtime = FakeRuntime(recordings=ACME_RECORDINGS)
        first = drain_understand_jobs(conn, runtime=runtime, embedding=runtime)
        assert first >= 1
        assert JobState.PENDING.value not in _understand_job_states(conn)
        assert JobState.CLAIMED.value not in _understand_job_states(conn)
        fingerprint = _entity_fingerprint(conn)
        obs = int(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])
        entities = int(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
        aliases = int(conn.execute("SELECT COUNT(*) FROM entity_aliases").fetchone()[0])
        chats = runtime.complete_calls
        assert drain_understand_jobs(conn, runtime=runtime, embedding=runtime) == 0
        assert runtime.complete_calls == chats
        assert _entity_fingerprint(conn) == fingerprint
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == obs
        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == entities
        assert conn.execute("SELECT COUNT(*) FROM entity_aliases").fetchone()[0] == aliases
        assert _has_entity(conn, typ="person", name="Priya Natarajan")
        assert _has_alias(conn, canonical="Internal Onboarding Guide", alias="Onboarding Guide")
    finally:
        conn.close()


def _working_day_id(conn: object, segment_id: str) -> str:
    row = conn.execute("SELECT working_day_id FROM segments WHERE id = ?", (segment_id,)).fetchone()
    assert row is not None
    return str(row[0])


def _day_row(conn: object, day_id: str) -> Any:
    return conn.execute(
        "SELECT state, consolidation_error, consolidated_at FROM working_days WHERE id = ?",
        (day_id,),
    ).fetchone()


def _consolidated_audit_count(conn: object) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE kind = ?", (KIND_CONSOLIDATED,)
        ).fetchone()[0]
    )


def _drain_acme(conn: object, *, now: datetime | None = None) -> int:
    close_open_segments(conn)
    enqueue_eligible_understand_jobs(conn, now=now)
    runtime = FakeRuntime(recordings=ACME_RECORDINGS)
    return drain_understand_jobs(conn, runtime=runtime, embedding=runtime, now=now)


def test_drain_uses_hour_long_understand_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E7: drain must claim understand jobs with UNDERSTAND_LEASE_SECONDS, not 60s."""
    seen: list[int] = []
    real_claim = claim

    def spy_claim(*args: object, **kwargs: object) -> object:
        lease = kwargs.get("lease_seconds")
        if isinstance(lease, int):
            seen.append(lease)
        return real_claim(*args, **kwargs)

    monkeypatch.setattr("watson.pipeline.understanding.claim", spy_claim)
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        _closed_segment(conn)
        enqueue_eligible_understand_jobs(conn)
        runtime = FakeRuntime()
        assert drain_understand_jobs(conn, runtime=runtime, embedding=runtime) == 1
        assert seen
        assert seen[0] == UNDERSTAND_LEASE_SECONDS
        assert seen[0] == 3600
        assert seen[0] != DEFAULT_LEASE_SECONDS
    finally:
        conn.close()


def test_retryable_job_failure_stops_drain_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D4: retryable JobFailure fails the job, stops the loop, and does not raise."""
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        first = _closed_segment(conn)
        day_id = _working_day_id(conn, first)
        second = _closed_segment(conn, day_id=day_id, started_at="2026-10-31T10:00:00Z")
        enqueue_eligible_understand_jobs(conn)
        calls = {"n": 0}

        def boom(*_a: object, **_k: object) -> object:
            calls["n"] += 1
            raise JobFailure("retryable drain stop", retryable=True)

        monkeypatch.setattr("watson.pipeline.understanding.understand_job", boom)
        completed = drain_understand_jobs(conn, runtime=FakeRuntime(), embedding=FakeRuntime())
        assert completed == 0
        assert calls["n"] == 1
        first_job = conn.execute(
            "SELECT state, attempts, last_error FROM jobs WHERE payload LIKE ?",
            (f"%{first}%",),
        ).fetchone()
        second_job = conn.execute(
            "SELECT state, attempts, last_error FROM jobs WHERE payload LIKE ?",
            (f"%{second}%",),
        ).fetchone()
        assert first_job["state"] == JobState.PENDING.value
        assert first_job["attempts"] == 1
        assert first_job["last_error"] == "JobFailure"
        assert second_job["state"] == JobState.PENDING.value
        assert second_job["attempts"] == 0
        assert second_job["last_error"] is None
    finally:
        conn.close()


def test_resolver_partial_write_rolls_back_before_park(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E6: rollback before parking so a partial resolver INSERT is not committed."""
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    sentinel = "E6 Sentinel Entity XYZ"
    try:
        _closed_segment(conn, merged="alpha ocr still editing")
        enqueue_eligible_understand_jobs(conn)

        def insert_then_crash(resolve_conn: object, *_a: object, **_k: object) -> list[object]:
            stamp = "2026-10-31T09:00:00Z"
            resolve_conn.execute(
                """
                INSERT INTO entities (
                    id, created_at, updated_at, type, canonical_name, description,
                    first_seen, last_seen, provisional, merged_into_id
                ) VALUES (?, ?, ?, 'person', ?, NULL, ?, ?, 1, NULL)
                """,
                (new_id(), stamp, stamp, sentinel, stamp, stamp),
            )
            raise RuntimeError("resolver partial write")

        monkeypatch.setattr("watson.pipeline.understanding.resolve_candidates", insert_then_crash)
        runtime = FakeRuntime(complete_queue=[dict(_RESOLVE_OUTPUT)])
        with pytest.raises(RuntimeError, match="resolver partial write"):
            drain_understand_jobs(conn, runtime=runtime, embedding=runtime)
        assert JobState.COMPLETED.value not in _understand_job_states(conn)
        assert JobState.PENDING.value in _understand_job_states(conn)
    finally:
        conn.close()
    fresh = connect(path, TEST_KEY, load_vec=False)
    try:
        assert not _has_entity(fresh, typ="person", name=sentinel)
        assert JobState.PENDING.value in _understand_job_states(fresh)
        assert fresh.execute("SELECT COUNT(*) FROM observations").fetchone()[0] >= 1
    finally:
        fresh.close()


def test_integrity_error_rebuilds_pending_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E3b: duplicate-insert IntegrityError still queues deferred resolve from raw_json."""
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        _closed_segment(conn, merged="alpha ocr still editing")
        enqueue_eligible_understand_jobs(conn)
        real_persist = _persist_observation

        def persist_then_integrity(*args: object, **kwargs: object) -> str:
            real_persist(*args, **kwargs)
            args[0].commit()  # type: ignore[union-attr]
            raise sqlcipher.IntegrityError("UNIQUE constraint failed: observations.segment_id")

        monkeypatch.setattr(
            "watson.pipeline.understanding._persist_observation", persist_then_integrity
        )
        runtime = FakeRuntime(complete_queue=[dict(_RESOLVE_OUTPUT)])
        assert drain_understand_jobs(conn, runtime=runtime, embedding=runtime) == 1
        assert _has_entity(conn, typ="person", name="Priya Natarajan")
        assert _has_entity(conn, typ="project", name="Harbor planning")
        assert JobState.COMPLETED.value in _understand_job_states(conn)
    finally:
        conn.close()


def test_poison_raw_json_fails_job_and_continues_drain(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """E9: one malformed observation fails that job; the rest of the drain continues."""
    caplog.set_level(logging.WARNING, logger="watson.pipeline.understanding")
    path = tmp_path / "watson.db"
    upgrade_head(path, TEST_KEY)
    conn = connect(path, TEST_KEY, load_vec=False)
    try:
        poison_segment = _closed_segment(conn, merged="poison ocr still editing")
        day_id = _working_day_id(conn, poison_segment)
        good_segment = _closed_segment(
            conn, merged="good ocr still editing", day_id=day_id, started_at="2026-10-31T10:00:00Z"
        )
        enqueue_eligible_understand_jobs(conn)
        stamp = "2026-10-31T09:00:00Z"
        conn.execute(
            """
            INSERT INTO observations (
                id, created_at, updated_at, segment_id, activity_type, description,
                confidence, model_id, prompt_version, pipeline_version, raw_json
            ) VALUES (?, ?, ?, ?, 'knowledge_work', 'poison', 0.1, 'fake',
                      'understanding/v2', ?, ?)
            """,
            (new_id(), stamp, stamp, poison_segment, PIPELINE_VERSION, "{not-json"),
        )
        conn.commit()
        runtime = FakeRuntime(complete_queue=[dict(_RESOLVE_OUTPUT)])
        completed = drain_understand_jobs(conn, runtime=runtime, embedding=runtime)
        assert completed == 1
        poison_job = conn.execute(
            "SELECT state, attempts, last_error FROM jobs WHERE payload LIKE ?",
            (f"%{poison_segment}%",),
        ).fetchone()
        good_job = conn.execute(
            "SELECT state, attempts, last_error FROM jobs WHERE payload LIKE ?",
            (f"%{good_segment}%",),
        ).fetchone()
        assert poison_job["attempts"] == 1
        assert poison_job["last_error"] == "JobFailure"
        assert poison_job["state"] in {JobState.PENDING.value, JobState.FAILED.value}
        assert good_job["state"] == JobState.COMPLETED.value
        assert _has_entity(conn, typ="person", name="Priya Natarajan")
        assert any("poison observation raw_json" in rec.message for rec in caplog.records)
        runtime = FakeRuntime(recordings=CONSOLIDATION_RECORDINGS)
        with pytest.raises(ConsolidationWait, match="understanding held"):
            consolidate_working_day(conn, day_id, runtime=runtime, embedding=runtime, now=_NOW)
        day = _day_row(conn, day_id)
        assert day["state"] != "consolidated"
        assert day["consolidated_at"] is None
        assert day["consolidation_error"] == "ConsolidationWait"
    finally:
        conn.close()


def test_sigkill_mid_drain_holds_consolidation_until_lease_recovered(tmp_path: Path) -> None:
    """Real SIGKILL: in-lease consolidation defers; after reclaim, entities match baseline."""
    killed_path = tmp_path / "killed.db"
    ready = tmp_path / "ready"
    upgrade_head(killed_path, TEST_KEY)
    conn = connect(killed_path, TEST_KEY, load_vec=False)
    try:
        _prepare_acme_q4(conn, tmp_path / "spool-kill")
        day_id = str(conn.execute("SELECT id FROM working_days").fetchone()[0])
        n_jobs = int(
            conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE kind = ?", (UNDERSTAND_JOB_KIND,)
            ).fetchone()[0]
        )
        assert n_jobs >= 1
    finally:
        conn.close()

    env = os.environ.copy()
    root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = os.pathsep.join([str(root / "src"), str(root), env.get("PYTHONPATH", "")])
    moment = datetime.now(UTC).replace(microsecond=0)
    proc = subprocess.Popen(
        [
            sys.executable,
            str(_SIGKILL_WORKER),
            str(killed_path),
            TEST_KEY.hex(),
            str(ACME_RECORDINGS),
            str(ready),
            moment.isoformat(),
        ],
        env=env,
        cwd=str(root),
    )
    try:
        deadline = time.time() + 60
        while not (ready.is_file() and ready.read_text(encoding="utf-8") == "ready"):
            if proc.poll() is not None:
                raise AssertionError(
                    f"drain worker exited before resolver ready: {proc.returncode}"
                )
            if time.time() > deadline:
                proc.kill()
                raise AssertionError("drain worker did not reach resolver boundary")
            time.sleep(0.05)
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
        assert proc.returncode == -signal.SIGKILL
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    conn = None
    last_exc: BaseException | None = None
    for _ in range(20):
        try:
            conn = connect(killed_path, TEST_KEY, load_vec=False)
            conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
            break
        except (sqlcipher.DatabaseError, DatabaseError, OSError) as exc:
            last_exc = exc
            if conn is not None:
                conn.close()
                conn = None
            time.sleep(0.05)
    if conn is None:
        raise AssertionError(f"could not reopen db after SIGKILL: {last_exc}")
    try:
        assert _understand_job_states(conn) == {JobState.CLAIMED.value}
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] >= 1
        assert not _has_entity(conn, typ="person", name="Priya Natarajan")
        assert not _has_alias(conn, canonical="Internal Onboarding Guide", alias="Onboarding Guide")
        audits_before = _consolidated_audit_count(conn)
        consolidator = FakeRuntime(recordings=CONSOLIDATION_RECORDINGS)
        with pytest.raises(ConsolidationWait, match="understanding held"):
            consolidate_working_day(
                conn, day_id, runtime=consolidator, embedding=consolidator, now=moment
            )
        day = _day_row(conn, day_id)
        assert day["state"] != "consolidated"
        assert day["consolidated_at"] is None
        assert day["consolidation_error"] == "ConsolidationWait"
        assert _consolidated_audit_count(conn) == audits_before
        recovered_at = moment + timedelta(hours=2)
        recovered = recover_expired_leases(conn, now=recovered_at)
        assert recovered == n_jobs
        assert JobState.CLAIMED.value not in _understand_job_states(conn)
        assert _drain_acme(conn, now=recovered_at) >= n_jobs
        assert JobState.PENDING.value not in _understand_job_states(conn)
        assert JobState.CLAIMED.value not in _understand_job_states(conn)
        consolidate_working_day(
            conn, day_id, runtime=consolidator, embedding=consolidator, now=recovered_at
        )
        day = _day_row(conn, day_id)
        assert day["state"] == "consolidated"
        assert day["consolidated_at"] is not None
        assert _consolidated_audit_count(conn) == audits_before + 1
        assert _has_entity(conn, typ="person", name="Priya Natarajan")
        assert _has_alias(conn, canonical="Internal Onboarding Guide", alias="Onboarding Guide")
        killed_fp = _entity_fingerprint(conn)
        killed_derived = derived_counts(conn, day_id)
        assert _understand_job_states(conn) == {JobState.COMPLETED.value}
    finally:
        conn.close()

    clean_path = tmp_path / "clean.db"
    upgrade_head(clean_path, TEST_KEY)
    clean = connect(clean_path, TEST_KEY, load_vec=False)
    try:
        _prepare_acme_q4(clean, tmp_path / "spool-clean-kill")
        clean_day = str(clean.execute("SELECT id FROM working_days").fetchone()[0])
        assert _drain_acme(clean) >= 1
        assert JobState.PENDING.value not in _understand_job_states(clean)
        assert JobState.CLAIMED.value not in _understand_job_states(clean)
        consolidator = FakeRuntime(recordings=CONSOLIDATION_RECORDINGS)
        consolidate_working_day(
            clean,
            clean_day,
            runtime=consolidator,
            embedding=consolidator,
            now=datetime.now(UTC),
        )
        assert _entity_fingerprint(clean) == killed_fp
        assert derived_counts(clean, clean_day) == killed_derived
        assert _day_row(clean, clean_day)["state"] == "consolidated"
    finally:
        clean.close()

"""Dashboard day page: summaries, citations, Unverified, states, Retry."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.replay_support import open_test_db
from tests.unit.test_consolidation import (
    NOW,
    _closed_ununderstood,
    _insert_claim,
    _insert_day,
    _insert_frame,
    _insert_observation,
    _insert_segment,
    _runtime,
    _seed_day,
)
from watson.config import load_config
from watson.dashboard.app import create_app
from watson.intelligence.runtime.select import RuntimeHold
from watson.memory.ids import new_id
from watson.pipeline.consolidation import consolidate_working_day, retry_working_day
from watson.pipeline.jobs import JobState, enqueue

INSTALL_TOKEN = "test-install-token-not-a-secret-for-ci"
ORIGIN = "http://127.0.0.1:8000"


def _client(runtime: object | None = None) -> TestClient:
    app = create_app(token=INSTALL_TOKEN, config=load_config(), port=8000)
    if runtime is not None:
        app.state.runtime = runtime
    client = TestClient(app, base_url=ORIGIN)
    code = app.state.launch_codes.mint()
    response = client.get(f"/auth?code={code}", follow_redirects=False)
    assert response.status_code == 303
    return client


def _csrf(page_text: str) -> str:
    marker = 'name="csrf_token" value="'
    start = page_text.index(marker) + len(marker)
    return page_text[start : page_text.index('"', start)]


def test_days_show_summary_citations_and_unverified(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        first = _seed_day(conn, date="2026-10-31")
        second_day = _seed_day(conn, date="2026-10-30")
        runtime = _runtime()
        consolidate_working_day(conn, first["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        consolidate_working_day(
            conn, second_day["day_id"], runtime=runtime, embedding=runtime, now=NOW
        )
    finally:
        conn.close()
    client = _client()
    latest = client.get("/days")
    assert latest.status_code == 200
    assert "2026-10-31" in latest.text
    assert "2026-10-30" in latest.text
    cited = re.findall(r'href="#claim-([0-9a-fA-F-]+)"', latest.text)
    assert cited
    for claim_id in cited:
        assert f'id="claim-{claim_id}"' in latest.text
    assert "Still editing: Acme proposal appendix" in latest.text
    assert "Unverified" in latest.text
    assert "Probably finished" in latest.text
    earlier = client.get("/days?date=2026-10-30")
    assert earlier.status_code == 200
    earlier_cited = re.findall(r'href="#claim-([0-9a-fA-F-]+)"', earlier.text)
    assert earlier_cited
    assert not set(cited) & set(earlier_cited)


def test_failed_and_closing_states_render(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        failed = _seed_day(conn, date="2026-10-30")
        closing = _seed_day(conn, date="2026-10-31")
        conn.execute(
            """
            UPDATE working_days
            SET state = 'failed', consolidation_error = 'ConsolidationError'
            WHERE id = ?
            """,
            (failed["day_id"],),
        )
        conn.execute(
            """
            UPDATE working_days
            SET state = 'closing', consolidation_error = 'ConsolidationWait'
            WHERE id = ?
            """,
            (closing["day_id"],),
        )
        conn.commit()
        failed_id = failed["day_id"]
    finally:
        conn.close()
    client = _client()
    closed = client.get("/days?date=2026-10-31")
    assert closed.status_code == 200
    assert "closing" in closed.text
    assert "ConsolidationWait" in closed.text
    assert "Retry" not in closed.text
    broken = client.get("/days?date=2026-10-30")
    assert broken.status_code == 200
    assert "failed" in broken.text
    assert "ConsolidationError" in broken.text
    assert "Retry" in broken.text
    assert f"/days/{failed_id}/retry" in broken.text


def test_retry_uses_existing_path(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        seeded = _seed_day(conn)
        conn.execute(
            """
            UPDATE working_days
            SET state = 'failed', consolidation_error = 'ConsolidationError'
            WHERE id = ?
            """,
            (seeded["day_id"],),
        )
        enqueue(
            conn,
            kind="consolidate",
            payload={"working_day_id": seeded["day_id"], "pipeline_version": 1},
            idempotency_key=f"consolidate:{seeded['day_id']}:1",
            now=NOW,
        )
        conn.execute(
            "UPDATE jobs SET state = ?, attempts = 5, last_error = ? WHERE kind = 'consolidate'",
            (JobState.FAILED.value, "ConsolidationError"),
        )
        conn.commit()
        day_id = seeded["day_id"]
    finally:
        conn.close()
    client = _client(runtime=_runtime())
    page = client.get("/days?date=2026-10-31")
    csrf = _csrf(page.text)
    retry = client.post(
        f"/days/{day_id}/retry",
        data={"csrf_token": csrf},
        headers={"Origin": ORIGIN},
        follow_redirects=False,
    )
    assert retry.status_code == 303
    shown = client.get("/days?date=2026-10-31")
    assert shown.status_code == 200
    assert "consolidated" in shown.text
    assert "[c:" in shown.text


def _insert_summary(
    conn: object,
    day_id: str,
    *,
    narrative: str,
    claim_ids: list[str] | None = None,
    unverified: list[dict[str, str]] | None = None,
    pipeline_version: int = 1,
) -> None:
    stamp = "2026-10-31T10:00:00Z"
    ids = claim_ids or []
    conn.execute(
        """
        INSERT INTO daily_summaries (
            id, created_at, updated_at, working_day_id, narrative_md,
            sections_json, claim_ids, prompt_version, pipeline_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'daily_summary/v1', ?)
        """,
        (
            new_id(),
            stamp,
            stamp,
            day_id,
            narrative,
            json.dumps(
                {
                    "paragraphs": [narrative],
                    "unverified": unverified or [],
                    "claim_ids": ids,
                }
            ),
            json.dumps(ids),
            pipeline_version,
        ),
    )
    conn.commit()


def test_empty_days_page(data_dir: Path, key_provider: object) -> None:
    open_test_db(data_dir).close()
    client = _client()
    page = client.get("/days")
    assert page.status_code == 200
    assert "no working day" in page.text


def test_production_retry_never_writes_fake_model_id(
    data_dir: Path, key_provider: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config()
    assert config.intelligence.runtime == "ollama"
    conn = open_test_db(data_dir)
    try:
        day_id, segment_id = _closed_ununderstood(conn)
        conn.execute(
            """
            UPDATE working_days
            SET state = 'failed', consolidation_error = 'ConsolidationError'
            WHERE id = ?
            """,
            (day_id,),
        )
        enqueue(
            conn,
            kind="consolidate",
            payload={"working_day_id": day_id, "pipeline_version": 1},
            idempotency_key=f"consolidate:{day_id}:1",
            now=NOW,
        )
        conn.execute(
            "UPDATE jobs SET state = ?, attempts = 5, last_error = ? WHERE kind = 'consolidate'",
            (JobState.FAILED.value, "ConsolidationError"),
        )
        conn.commit()
    finally:
        conn.close()

    seen: dict[str, Any] = {}
    real = retry_working_day

    def wrapped(*args: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        return real(*args, **kwargs)

    def held(*_a: object, **_k: object) -> RuntimeHold:
        return RuntimeHold(held=True, reason="ollama down", jobs_updated=0)

    monkeypatch.setattr("watson.pipeline.consolidation.retry_working_day", wrapped)
    monkeypatch.setattr("watson.intelligence.runtime.hold_if_unavailable", held)

    client = _client()
    page = client.get("/days?date=2026-10-31")
    csrf = _csrf(page.text)
    retry = client.post(
        f"/days/{day_id}/retry",
        data={"csrf_token": csrf},
        headers={"Origin": ORIGIN},
        follow_redirects=False,
    )
    assert retry.status_code == 303
    assert seen.get("spool_dir") == config.spool_dir
    assert seen.get("settings") is not None
    assert seen["settings"].pipeline_version == 1
    assert seen.get("runtime") is None

    conn = open_test_db(data_dir)
    try:
        fake = conn.execute(
            "SELECT COUNT(*) FROM observations WHERE model_id = 'fake'"
        ).fetchone()[0]
        assert fake == 0
        row = conn.execute(
            "SELECT state, consolidation_error FROM working_days WHERE id = ?",
            (day_id,),
        ).fetchone()
        assert row["state"] == "closing"
        assert row["consolidation_error"] == "RuntimeUnavailable"
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM observations WHERE segment_id = ?",
                (segment_id,),
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


def test_days_escape_hostile_summary_claim_and_evidence(
    data_dir: Path, key_provider: object
) -> None:
    conn = open_test_db(data_dir)
    try:
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
            text='Claim <script>pwn()</script> attr="quoted"',
            evidence=False,
        )
        uncited_text = (
            'Uncited <script>unverified_xss()</script> onclick="exfil" and "quotes"'
        )
        uncited_id = _insert_claim(
            conn,
            observation_id,
            day_id,
            segment_id,
            frame_id,
            kind="outcome",
            assertion="inferred",
            text=uncited_text,
            evidence=False,
        )
        stamp = "2026-10-31T10:00:00Z"
        conn.execute(
            """
            INSERT INTO claim_evidence (
                id, created_at, updated_at, claim_id, segment_id, frame_id,
                snippet, snippet_kind, line_start, line_end
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ocr', 1, 1)
            """,
            (
                new_id(),
                stamp,
                stamp,
                claim_id,
                segment_id,
                frame_id,
                'Evidence <img src=x onerror="alert(1)"> and "quotes"',
            ),
        )
        _insert_summary(
            conn,
            day_id,
            narrative=f"Hello <script>alert(1)</script> cited [c:{claim_id}].",
            claim_ids=[claim_id],
            unverified=[{"id": uncited_id, "kind": "outcome", "text": uncited_text}],
        )
    finally:
        conn.close()
    client = _client()
    page = client.get("/days?date=2026-10-31")
    assert page.status_code == 200
    assert "<script>" not in page.text
    assert "<img" not in page.text
    assert "&lt;script&gt;" in page.text
    assert "onerror" in page.text
    assert "&lt;img" in page.text
    assert "attr=&#34;quoted&#34;" in page.text or "attr=&quot;quoted&quot;" in page.text
    assert f'id="unverified-{uncited_id}"' in page.text
    assert "<script>unverified_xss()" not in page.text
    assert "&lt;script&gt;unverified_xss()" in page.text
    assert 'onclick="exfil"' not in page.text
    assert "onclick=&#34;exfil&#34;" in page.text or "onclick=&quot;exfil&quot;" in page.text


def test_days_page_scopes_summary_to_pipeline_version(
    data_dir: Path, key_provider: object
) -> None:
    conn = open_test_db(data_dir)
    try:
        day_id = _insert_day(conn)
        frame_id = _insert_frame(conn, day_id)
        segment_id = _insert_segment(conn, day_id, frame_id)
        observation_id = _insert_observation(conn, segment_id)
        v1 = _insert_claim(
            conn,
            observation_id,
            day_id,
            segment_id,
            frame_id,
            kind="outcome",
            assertion="observed",
            text="v1 claim body",
        )
        v2 = _insert_claim(
            conn,
            observation_id,
            day_id,
            segment_id,
            frame_id,
            kind="outcome",
            assertion="observed",
            text="v2 claim leaked",
            pipeline_version=2,
        )
        stamp = "2026-10-31T10:00:00Z"
        conn.execute(
            """
            INSERT INTO claim_evidence (
                id, created_at, updated_at, claim_id, segment_id, frame_id,
                snippet, snippet_kind, line_start, line_end
            ) VALUES (?, ?, ?, ?, ?, ?, 'v2 quote leaked', 'ocr', 1, 1)
            """,
            (new_id(), stamp, stamp, v2, segment_id, frame_id),
        )
        _insert_summary(
            conn,
            day_id,
            narrative="v2 narrative leaked",
            claim_ids=[v2],
            pipeline_version=2,
        )
        _insert_summary(conn, day_id, narrative="v1 narrative only", claim_ids=[v1])
    finally:
        conn.close()
    client = _client()
    page = client.get("/days?date=2026-10-31")
    assert page.status_code == 200
    assert "v1 narrative only" in page.text
    assert "v1 claim body" in page.text
    assert "v2 narrative leaked" not in page.text
    assert "v2 claim leaked" not in page.text
    assert "v2 quote leaked" not in page.text


def test_unverified_section_hidden_when_empty(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
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
        _insert_summary(
            conn,
            day_id,
            narrative=f"Cited work landed [c:{claim_id}].",
            claim_ids=[claim_id],
        )
    finally:
        conn.close()
    client = _client()
    page = client.get("/days?date=2026-10-31")
    assert page.status_code == 200
    assert "Unverified" not in page.text
    assert "Appendix drafted" in page.text
    assert "[c:" in page.text

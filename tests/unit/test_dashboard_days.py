"""Dashboard day page: summaries, citations, Unverified, states, Retry."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from tests.replay_support import open_test_db
from tests.unit.test_consolidation import NOW, _runtime, _seed_day
from watson.config import load_config
from watson.dashboard.app import create_app
from watson.pipeline.consolidation import consolidate_working_day
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


def test_empty_days_page(data_dir: Path, key_provider: object) -> None:
    open_test_db(data_dir).close()
    client = _client()
    page = client.get("/days")
    assert page.status_code == 200
    assert "no working day" in page.text

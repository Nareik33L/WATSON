"""Today timeline renders replayed segments behind the existing auth."""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from tests.replay_support import open_test_db, write_mini_fixture
from watson.config import load_config
from watson.dashboard.app import create_app
from watson.pipeline.replay import replay_fixture
from watson.pipeline.today import collect_today

INSTALL_TOKEN = "test-install-token-not-a-secret-for-ci"
TEST_PORT = 8000
ORIGIN = f"http://127.0.0.1:{TEST_PORT}"
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"


def _client() -> TestClient:
    app = create_app(token=INSTALL_TOKEN, config=load_config(), port=TEST_PORT)
    return TestClient(app, base_url=ORIGIN, raise_server_exceptions=True)


def _authed(client: TestClient) -> None:
    code = client.app.state.launch_codes.mint()
    response = client.get(f"/auth?code={code}", follow_redirects=False)
    assert response.status_code == 303


def test_today_requires_auth() -> None:
    client = _client()
    page = client.get("/today", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/auth"
    api = client.get("/api/today")
    assert api.status_code == 401
    htmx = client.get("/today/partial", headers={"HX-Request": "true"})
    assert htmx.status_code == 401


def test_replayed_fixture_renders_timeline(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    fixture = write_mini_fixture(tmp_path, frames=2)
    conn = open_test_db(data_dir)
    try:
        replay_fixture(fixture, conn, instant=True, spool_dir=tmp_path / "spool")
        report = collect_today(conn)
        assert report.date == "2026-10-31"
        assert len(report.segments) == 1
        segment = report.segments[0]
        assert segment.app_name == "Lumen Mail"
        assert segment.window_title == "Inbox"
        assert segment.dwell_seconds == 60
        assert "09:01" in segment.range_label()
        assert "09:02" in segment.range_label()

        client = _client()
        _authed(client)
        page = client.get("/today")
        assert page.status_code == 200
        assert "/static/htmx.min.js" in page.text
        assert 'hx-get="/today/partial"' in page.text
        assert "Lumen Mail" in page.text
        assert "Inbox" in page.text
        assert "09:01" in page.text
        assert "09:02" in page.text
        assert "1m" in page.text
        payload = client.get("/api/today").json()
        assert payload["date"] == "2026-10-31"
        assert len(payload["segments"]) == 1
        assert payload["segments"][0]["app_name"] == "Lumen Mail"
        assert payload["segments"][0]["window_title"] == "Inbox"
        assert "merged_text" not in payload["segments"][0]
        assert "ocr" not in page.text.lower()
        partial = client.get("/today/partial", headers={"HX-Request": "true"})
        assert partial.status_code == 200
        assert "Lumen Mail" in partial.text
    finally:
        conn.close()


def test_sensitive_traps_excluded_content_stays_off_today(
    data_dir: Path, key_provider: object
) -> None:
    fixture = FIXTURE_ROOT / "day_sensitive_traps"
    truth = yaml.safe_load((fixture / "truth.yaml").read_text(encoding="utf-8"))
    conn = open_test_db(data_dir)
    try:
        replay_fixture(fixture, conn, instant=True, spool_dir=data_dir / "spool-traps-today")
        client = _client()
        _authed(client)
        page = client.get("/today")
        api = client.get("/api/today")
        assert page.status_code == 200
        body = page.text + str(api.json())
        assert "Sign in — Harbor Credit" not in body
        assert "Harbor Credit" not in body
        assert "Personal vault" not in body
        assert "com.apple.Passwords" not in body
        assert "4111111111111111" not in body
        assert "cedar-trap-pass-9f3" not in body
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in body
        for expected in truth["expected_segments"]:
            assert expected["window_title"] in page.text
    finally:
        conn.close()

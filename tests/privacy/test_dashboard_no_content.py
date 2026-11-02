"""Status page never renders screen content; CSP present; no-network safe."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.replay_support import distinctive_ocr_texts, open_test_db, write_mini_fixture
from tests.unit.test_consolidation import (
    _insert_claim,
    _insert_day,
    _insert_frame,
    _insert_segment,
)
from tests.unit.test_dashboard_pages import _insert_entity, _observation_with_candidates
from watson.config import load_config
from watson.dashboard.app import CSP, PACKAGE_DIR, create_app
from watson.pipeline.replay import replay_fixture
from watson.privacy.contentscan import content_field_tokens, scan_output_paths, scan_text
from watson.privacy.network import runtime_network_guard

INSTALL_TOKEN = "privacy-dashboard-token"
PLANTED = "PLANTED_DASHBOARD_OCR_MUST_NEVER_RENDER"
SENTINEL = "PLANTED_TIER2_LEAK_SENTINEL_9f3a"
HISTORY_REASON = "HISTORY_REASON_VISIBLE_ON_PAGE"
ENTITY_DESCRIPTION = "ENTITY_DESCRIPTION_VISIBLE_ON_PAGE"


def _authed_client() -> TestClient:
    app = create_app(token=INSTALL_TOKEN, config=load_config(), port=8000)
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    code = app.state.launch_codes.mint()
    response = client.get(f"/auth?code={code}", follow_redirects=False)
    assert response.status_code == 303
    return client


def test_dashboard_sources_have_no_raw_content_fields() -> None:
    py_files = list((PACKAGE_DIR).rglob("*.py"))
    templates = list((PACKAGE_DIR / "templates").glob("*.html"))
    assert py_files and templates
    assert scan_output_paths(py_files) == []
    for path in templates:
        assert scan_text(path.read_text(encoding="utf-8"), filename=str(path)) == []


def test_responses_have_csp_and_no_content_fields(
    data_dir: Path, key_provider: object, tmp_path: Path
) -> None:
    fixture = write_mini_fixture(tmp_path, frames=2, planted=PLANTED)
    phrases = distinctive_ocr_texts(fixture, min_len=16)
    assert PLANTED in " ".join(phrases) or any(PLANTED in item for item in phrases)
    conn = open_test_db(data_dir)
    try:
        replay_fixture(fixture, conn, instant=True, spool_dir=tmp_path / "spool")
        conn.execute("UPDATE frame_text SET text_redacted = ?", (SENTINEL,))
        day_id = _insert_day(conn, date="2026-11-01", state="failed")
        conn.execute(
            "UPDATE working_days SET consolidation_error = ? WHERE id = ?",
            (HISTORY_REASON, day_id),
        )
        frame_id = _insert_frame(conn, day_id)
        conn.execute("UPDATE frame_text SET text_redacted = ?", (SENTINEL,))
        segment_id = _insert_segment(conn, day_id, frame_id)
        observation_id = _observation_with_candidates(
            conn,
            segment_id,
            description="Call with Priya about pricing",
            projects=["Acme Q4 Proposal"],
            people=["Priya Natarajan"],
        )
        _insert_claim(
            conn,
            observation_id,
            day_id,
            segment_id,
            frame_id,
            kind="person_interaction",
            assertion="observed",
            text="Talked to Priya about pricing",
            evidence=False,
        )
        project_id = _insert_entity(
            conn,
            name="Acme Q4 Proposal",
            typ="project",
            description=ENTITY_DESCRIPTION,
        )
        person_id = _insert_entity(conn, name="Priya Natarajan", typ="person")
        conn.commit()
        with runtime_network_guard():
            client = _authed_client()
            page = client.get("/")
            api = client.get("/api/status")
            partial = client.get("/status/partial", headers={"HX-Request": "true"})
            today = client.get("/today")
            today_api = client.get("/api/today")
            today_partial = client.get("/today/partial", headers={"HX-Request": "true"})
            days = client.get("/days")
            history = client.get("/history")
            projects = client.get("/projects")
            people = client.get("/people")
            project_detail = client.get(f"/projects/{project_id}")
            person_detail = client.get(f"/people/{person_id}")
            history_api = client.get("/api/history")
            entities_api = client.get("/api/entities?type=project")
        assert HISTORY_REASON in history.text
        assert ENTITY_DESCRIPTION in project_detail.text
        assert "Acme Q4 Proposal" in projects.text
        assert "Priya Natarajan" in people.text
        for response in (
            page,
            api,
            partial,
            today,
            today_api,
            today_partial,
            days,
            history,
            projects,
            people,
            project_detail,
            person_detail,
            history_api,
            entities_api,
        ):
            assert response.status_code == 200
            assert response.headers["content-security-policy"] == CSP
            text = response.content.decode("utf-8")
            assert content_field_tokens(text) == []
            assert PLANTED not in text
            assert SENTINEL not in text
            for phrase in phrases:
                assert phrase not in text
        payload = api.json()
        assert "frame_text" not in payload
        assert "ocr" not in payload
        assert "snippet" not in payload
        assert "text_redacted" not in payload
        dumped = str(payload)
        assert PLANTED not in dumped
        assert SENTINEL not in dumped
        assert SENTINEL not in history.text
        assert SENTINEL not in project_detail.text
    finally:
        conn.close()


def test_htmx_is_vendored_not_cdn() -> None:
    htmx = PACKAGE_DIR / "static" / "htmx.min.js"
    assert htmx.is_file()
    text = htmx.read_text(encoding="utf-8")
    assert len(text) > 1000
    assert "htmx" in text.lower()
    html = (PACKAGE_DIR / "templates" / "base.html").read_text(encoding="utf-8")
    assert "/static/htmx.min.js" in html
    assert "/static/dashboard.js" in html
    assert "http://" not in html
    assert "https://" not in html
    dashboard_js = (PACKAGE_DIR / "static" / "dashboard.js").read_text(encoding="utf-8")
    assert "includeIndicatorStyles = false" in dashboard_js

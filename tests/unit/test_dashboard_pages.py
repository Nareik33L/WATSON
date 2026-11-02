"""M4a dashboard pages: Today observations, History, Projects, People."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from tests.replay_support import open_test_db
from tests.unit.test_consolidation import (
    _insert_claim,
    _insert_day,
    _insert_frame,
    _insert_observation,
    _insert_segment,
)
from watson.config import load_config
from watson.dashboard.app import PACKAGE_DIR, create_app
from watson.memory.ids import new_id

INSTALL_TOKEN = "test-install-token-not-a-secret-for-ci"
ORIGIN = "http://127.0.0.1:8000"
CSS = (PACKAGE_DIR / "static" / "status.css").read_text(encoding="utf-8")


def _client() -> TestClient:
    app = create_app(token=INSTALL_TOKEN, config=load_config(), port=8000)
    client = TestClient(app, base_url=ORIGIN)
    code = app.state.launch_codes.mint()
    response = client.get(f"/auth?code={code}", follow_redirects=False)
    assert response.status_code == 303
    return client


def _insert_entity(
    conn: object,
    *,
    name: str,
    typ: str = "project",
    alias: str | None = None,
    provisional: int = 0,
    merged_into_id: str | None = None,
    description: str | None = None,
) -> str:
    entity_id, stamp = new_id(), "2026-10-31T09:00:00Z"
    conn.execute(
        """
        INSERT INTO entities (
            id, created_at, updated_at, type, canonical_name, description,
            first_seen, last_seen, provisional, merged_into_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_id,
            stamp,
            stamp,
            typ,
            name,
            description,
            stamp,
            stamp,
            provisional,
            merged_into_id,
        ),
    )
    if alias:
        conn.execute(
            """
            INSERT INTO entity_aliases (
                id, created_at, updated_at, entity_id, alias, source
            ) VALUES (?, ?, ?, ?, ?, 'model')
            """,
            (new_id(), stamp, stamp, entity_id, alias),
        )
    conn.commit()
    return entity_id


def _observation_with_candidates(
    conn: object,
    segment_id: str,
    *,
    description: str,
    projects: list[str],
    people: list[str],
    pipeline_version: int = 1,
) -> str:
    obs_id = _insert_observation(conn, segment_id, description=description)
    raw = json.dumps(
        {
            "activity_type": "knowledge_work",
            "description": description,
            "project_candidates": projects,
            "people_candidates": people,
            "claims": [],
            "confidence": 0.8,
        }
    )
    conn.execute("UPDATE observations SET raw_json = ? WHERE id = ?", (raw, obs_id))
    conn.commit()
    if pipeline_version != 1:
        conn.execute(
            "UPDATE observations SET pipeline_version = ? WHERE id = ?",
            (pipeline_version, obs_id),
        )
        conn.commit()
    return obs_id


def _insert_summary(conn: object, day_id: str, narrative: str, claim_ids: list[str]) -> None:
    stamp = "2026-10-31T18:00:00Z"
    conn.execute(
        """
        INSERT INTO daily_summaries (
            id, created_at, updated_at, working_day_id, narrative_md,
            sections_json, claim_ids, prompt_version, pipeline_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'daily_summary/v1', 1)
        """,
        (
            new_id(),
            stamp,
            stamp,
            day_id,
            narrative,
            json.dumps({"paragraphs": [narrative], "unverified": [], "claim_ids": claim_ids}),
            json.dumps(claim_ids),
        ),
    )
    conn.commit()


def test_new_pages_require_auth() -> None:
    app = create_app(token=INSTALL_TOKEN, config=load_config(), port=8000)
    client = TestClient(app, base_url=ORIGIN)
    for path in ("/history", "/projects", "/people"):
        page = client.get(path, follow_redirects=False)
        assert page.status_code == 303, path
        assert page.headers["location"] == "/auth"


def test_css_is_usable_at_1280px() -> None:
    assert "max-width: min(1200px, 100%)" in CSS
    assert "overflow-wrap: anywhere" in CSS
    assert "flex-wrap: wrap" in CSS
    assert ".table-wrap" in CSS
    assert "table-layout: fixed" in CSS


def test_today_shows_observations_and_summary(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        day_id = _insert_day(conn, date="2026-10-31")
        frame_id = _insert_frame(conn, day_id)
        segment_id = _insert_segment(conn, day_id, frame_id)
        observation_id = _observation_with_candidates(
            conn,
            segment_id,
            description="Editing the Acme Q4 proposal draft.",
            projects=["Acme Q4 Proposal"],
            people=[],
        )
        claim_id = _insert_claim(
            conn,
            observation_id,
            day_id,
            segment_id,
            frame_id,
            kind="activity",
            assertion="observed",
            text="Edited the Acme draft",
        )
        _insert_summary(
            conn, day_id, f"Work continued on Acme [c:{claim_id}].", [claim_id]
        )
    finally:
        conn.close()
    client = _client()
    page = client.get("/today")
    assert page.status_code == 200
    assert "Editing the Acme Q4 proposal draft." in page.text
    assert "knowledge_work" in page.text
    assert "Work continued on Acme" in page.text
    assert f'href="#claim-{claim_id}"' in page.text
    assert f'id="claim-{claim_id}"' in page.text
    assert "Edited the Acme draft" in page.text
    payload = client.get("/api/today").json()
    assert payload["date"] == "2026-10-31"
    assert payload["summary_present"] is True
    assert payload["segments"][0]["observation"]["description"] == (
        "Editing the Acme Q4 proposal draft."
    )
    assert "merged_text" not in str(payload)


def test_today_without_observations_still_shows_segments(
    data_dir: Path, key_provider: object
) -> None:
    conn = open_test_db(data_dir)
    try:
        day_id = _insert_day(conn)
        frame_id = _insert_frame(conn, day_id)
        _insert_segment(conn, day_id, frame_id)
    finally:
        conn.close()
    client = _client()
    page = client.get("/today")
    assert page.status_code == 200
    assert "Pages" in page.text
    assert "no summary" in page.text
    payload = client.get("/api/today").json()
    assert "observation" not in payload["segments"][0]


def test_history_lists_days_with_state_and_day_links(
    data_dir: Path, key_provider: object
) -> None:
    conn = open_test_db(data_dir)
    try:
        first = _insert_day(conn, date="2026-10-30", state="failed")
        conn.execute(
            "UPDATE working_days SET consolidation_error = 'ConsolidationError' WHERE id = ?",
            (first,),
        )
        second = _insert_day(conn, date="2026-10-31", state="consolidated")
        frame_id = _insert_frame(conn, second)
        segment_id = _insert_segment(conn, second, frame_id)
        observation_id = _insert_observation(conn, segment_id)
        _insert_summary(conn, second, "Quiet day.", [])
        conn.commit()
    finally:
        conn.close()
    client = _client()
    page = client.get("/history")
    assert page.status_code == 200
    assert 'href="/days?date=2026-10-31"' in page.text
    assert 'href="/days?date=2026-10-30"' in page.text
    assert "consolidated" in page.text
    assert "failed" in page.text
    assert "ConsolidationError" in page.text
    assert ">yes<" in page.text
    payload = client.get("/api/history").json()
    dates = [item["date"] for item in payload["days"]]
    assert dates == ["2026-10-31", "2026-10-30"]
    assert payload["days"][0]["has_summary"] is True
    assert payload["days"][0]["observation_count"] == 1
    assert payload["days"][1]["has_summary"] is False
    assert observation_id  # seeded


def test_projects_and_people_are_separate_read_only(
    data_dir: Path, key_provider: object
) -> None:
    conn = open_test_db(data_dir)
    try:
        day_id = _insert_day(conn)
        frame_id = _insert_frame(conn, day_id)
        segment_id = _insert_segment(conn, day_id, frame_id)
        observation_id = _observation_with_candidates(
            conn,
            segment_id,
            description="Call with Priya about Acme",
            projects=["Acme Q4 Proposal"],
            people=["Priya Natarajan"],
        )
        claim_id = _insert_claim(
            conn,
            observation_id,
            day_id,
            segment_id,
            frame_id,
            kind="person_interaction",
            assertion="observed",
            text="Talked to Priya about pricing",
        )
        project_id = _insert_entity(
            conn, name="Acme Q4 Proposal", alias="Acme", typ="project"
        )
        person_id = _insert_entity(
            conn, name="Priya Natarajan", alias="Priya", typ="person"
        )
        other = _insert_entity(conn, name="Ghost Deal", typ="project")
        merged = _insert_entity(
            conn, name="Old Acme", typ="project", merged_into_id=project_id
        )
        stamp = "2026-10-31T18:00:00Z"
        conn.execute(
            """
            INSERT INTO entity_history (
                id, created_at, updated_at, entity_id, working_day_id, delta_text,
                claim_ids, prompt_version, pipeline_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'extract/v1', 1)
            """,
            (
                new_id(),
                stamp,
                stamp,
                project_id,
                day_id,
                "Pricing appendix moved forward",
                json.dumps([claim_id]),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    client = _client()
    projects = client.get("/projects")
    assert projects.status_code == 200
    assert "Acme Q4 Proposal" in projects.text
    assert "also" in projects.text
    assert "Acme" in projects.text
    assert "Priya Natarajan" not in projects.text
    assert "Old Acme" not in projects.text
    assert "Ghost Deal" in projects.text
    people = client.get("/people")
    assert people.status_code == 200
    assert "Priya Natarajan" in people.text
    assert "Acme Q4 Proposal" not in people.text
    detail = client.get(f"/projects/{project_id}")
    assert detail.status_code == 200
    assert "Talked to Priya about pricing" in detail.text
    assert "Still editing: Acme proposal appendix" in detail.text
    assert "Pricing appendix moved forward" in detail.text
    assert 'href="/days?date=2026-10-31"' in detail.text
    person = client.get(f"/people/{person_id}")
    assert person.status_code == 200
    assert "Talked to Priya about pricing" in person.text
    ghost = client.get(f"/projects/{other}")
    assert ghost.status_code == 200
    assert "no linked claims" in ghost.text
    missing = client.get(f"/projects/{merged}")
    assert "not found" in missing.text
    assert 'method="post"' not in detail.text.lower()
    assert 'method="post"' not in person.text.lower()
    assert 'method="post"' not in people.text.lower()


def test_entity_links_ignore_other_pipeline_version(
    data_dir: Path, key_provider: object
) -> None:
    conn = open_test_db(data_dir)
    try:
        day_id = _insert_day(conn)
        frame_id = _insert_frame(conn, day_id)
        segment_id = _insert_segment(conn, day_id, frame_id)
        v2 = _observation_with_candidates(
            conn,
            segment_id,
            description="v2 leaked",
            projects=["Acme Q4 Proposal"],
            people=[],
            pipeline_version=2,
        )
        _insert_claim(
            conn,
            v2,
            day_id,
            segment_id,
            frame_id,
            kind="activity",
            assertion="observed",
            text="v2 claim leaked",
            pipeline_version=2,
        )
        project_id = _insert_entity(conn, name="Acme Q4 Proposal", typ="project")
    finally:
        conn.close()
    client = _client()
    page = client.get(f"/projects/{project_id}")
    assert "v2 claim leaked" not in page.text
    assert "v2 leaked" not in page.text


def test_pages_escape_hostile_text(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        day_id = _insert_day(conn, state="failed")
        conn.execute(
            """
            UPDATE working_days
            SET consolidation_error = 'fail <script>hist()</script>'
            WHERE id = ?
            """,
            (day_id,),
        )
        frame_id = _insert_frame(conn, day_id)
        segment_id = _insert_segment(conn, day_id, frame_id)
        observation_id = _observation_with_candidates(
            conn,
            segment_id,
            description='Obs <script>pwn()</script> attr="x"',
            projects=['Acme <img src=x onerror="q">'],
            people=[],
        )
        claim_id = _insert_claim(
            conn,
            observation_id,
            day_id,
            segment_id,
            frame_id,
            kind="activity",
            assertion="observed",
            text='Claim <script>c()</script> onclick="e"',
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
                'Quote <script>q()</script> and "quotes"',
            ),
        )
        project_id = _insert_entity(
            conn,
            name='Acme <img src=x onerror="q">',
            alias='Alias <script>a()</script>',
            typ="project",
        )
        _insert_summary(
            conn,
            day_id,
            f"Hello <script>s()</script> [c:{claim_id}].",
            [claim_id],
        )
        conn.commit()
    finally:
        conn.close()
    client = _client()
    bodies = [
        client.get("/today").text,
        client.get("/history").text,
        client.get("/projects").text,
        client.get(f"/projects/{project_id}").text,
    ]
    joined = "\n".join(bodies)
    assert "<script>" not in joined
    assert "&lt;script&gt;" in joined
    assert "<img" not in joined
    today = bodies[0]
    assert "onerror" in today or "&lt;img" in joined
    assert 'onclick="e"' not in joined
    assert "Obs" in today


def test_nav_lists_m4a_pages(data_dir: Path, key_provider: object) -> None:
    open_test_db(data_dir).close()
    client = _client()
    page = client.get("/")
    assert 'href="/history"' in page.text
    assert 'href="/projects"' in page.text
    assert 'href="/people"' in page.text
    assert 'href="/today"' in page.text
    assert "no working days" in client.get("/history").text
    assert "no projects" in client.get("/projects").text
    assert "no people" in client.get("/people").text
    assert "capture-state" in page.text

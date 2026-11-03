"""M4a dashboard pages: Today observations, History, Projects, People."""

from __future__ import annotations

import hashlib
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
from watson.pipeline.frames import PIPELINE_VERSION

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
    pipeline_version: int = PIPELINE_VERSION,
) -> str:
    obs_id = _insert_observation(
        conn, segment_id, description=description, pipeline_version=pipeline_version
    )
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
    return obs_id


def _insert_summary(
    conn: object,
    day_id: str,
    narrative: str,
    claim_ids: list[str],
    *,
    unverified: list[dict[str, str]] | None = None,
    pipeline_version: int = PIPELINE_VERSION,
) -> None:
    stamp = "2026-10-31T18:00:00Z"
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
                    "claim_ids": claim_ids,
                }
            ),
            json.dumps(claim_ids),
            pipeline_version,
        ),
    )
    conn.commit()


def _insert_entity_history(
    conn: object,
    entity_id: str,
    day_id: str,
    *,
    delta: str,
    claim_ids: list[str],
    pipeline_version: int = PIPELINE_VERSION,
) -> None:
    stamp = "2026-10-31T18:00:00Z"
    conn.execute(
        """
        INSERT INTO entity_history (
            id, created_at, updated_at, entity_id, working_day_id, delta_text,
            claim_ids, prompt_version, pipeline_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'extract/v1', ?)
        """,
        (
            new_id(),
            stamp,
            stamp,
            entity_id,
            day_id,
            delta,
            json.dumps(claim_ids),
            pipeline_version,
        ),
    )
    conn.commit()


def _csrf(html: str) -> str:
    marker = 'name="csrf-token" content="'
    start = html.index(marker) + len(marker)
    token = html[start : html.index('"', start)]
    assert token
    return token


def _db_fingerprint(conn: object) -> dict[str, tuple[int, str]]:
    names = [
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    snapshot: dict[str, tuple[int, str]] = {}
    for name in names:
        rows = conn.execute(f"SELECT * FROM [{name}] ORDER BY rowid").fetchall()
        digest = hashlib.sha256()
        for row in rows:
            digest.update(repr(tuple(row)).encode("utf-8"))
            digest.update(b"\n")
        snapshot[name] = (len(rows), digest.hexdigest())
    return snapshot


def test_new_pages_require_auth() -> None:
    app = create_app(token=INSTALL_TOKEN, config=load_config(), port=8000)
    client = TestClient(app, base_url=ORIGIN)
    for path in ("/history", "/projects", "/people", "/memory"):
        page = client.get(path, follow_redirects=False)
        assert page.status_code == 303, path
        assert page.headers["location"] == "/auth"


def test_css_is_usable_at_1280px() -> None:
    assert "max-width: min(1200px, 100%)" in CSS
    assert "overflow-wrap: anywhere" in CSS
    assert "flex-wrap: wrap" in CSS
    assert ".table-wrap" in CSS
    assert "table-layout: fixed" in CSS
    assert ".claim-actions" in CSS


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
        _insert_summary(conn, day_id, f"Work continued on Acme [c:{claim_id}].", [claim_id])
    finally:
        conn.close()
    client = _client()
    page = client.get("/today")
    assert page.status_code == 200
    assert "<title>W.A.T.S.O.N today</title>" in page.text
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


def test_history_lists_days_with_state_and_day_links(data_dir: Path, key_provider: object) -> None:
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


def test_projects_and_people_are_separate_read_only(data_dir: Path, key_provider: object) -> None:
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
        project_id = _insert_entity(conn, name="Acme Q4 Proposal", alias="Acme", typ="project")
        person_id = _insert_entity(conn, name="Priya Natarajan", alias="Priya", typ="person")
        other = _insert_entity(conn, name="Ghost Deal", typ="project")
        merged = _insert_entity(conn, name="Old Acme", typ="project", merged_into_id=project_id)
        _insert_entity_history(
            conn,
            project_id,
            day_id,
            delta="Pricing appendix moved forward",
            claim_ids=[claim_id],
        )
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
    assert missing.status_code == 404
    assert "not found" in missing.text
    unknown = client.get("/people/no-such-person")
    assert unknown.status_code == 404
    assert "not found" in unknown.text
    assert 'method="post"' in detail.text.lower()
    assert "/entities/" in detail.text
    assert 'method="post"' in person.text.lower()
    assert 'method="post"' not in people.text.lower()
    assert 'method="post"' not in projects.text.lower()


def test_entity_links_ignore_other_pipeline_version(data_dir: Path, key_provider: object) -> None:
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
            pipeline_version=99,
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
            pipeline_version=99,
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
        unverified_id = _insert_claim(
            conn,
            observation_id,
            day_id,
            segment_id,
            frame_id,
            kind="outcome",
            assertion="inferred",
            text='Unverified <script>unv()</script> onclick="x"',
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
            alias="Alias <script>a()</script>",
            typ="project",
        )
        _insert_summary(
            conn,
            day_id,
            f"Hello <script>s()</script> [c:{claim_id}].",
            [claim_id],
            unverified=[
                {
                    "id": unverified_id,
                    "kind": "outcome",
                    "text": 'Unverified <script>unv()</script> onclick="x"',
                }
            ],
        )
        _insert_entity_history(
            conn,
            project_id,
            day_id,
            delta='Delta <script>delta()</script> onclick="d"',
            claim_ids=[claim_id],
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
        client.get("/memory").text,
    ]
    joined = "\n".join(bodies)
    assert "<script>" not in joined
    assert "&lt;script&gt;" in joined
    assert "<img" not in joined
    today = bodies[0]
    detail = bodies[3]
    assert "onerror" in today or "&lt;img" in joined
    assert 'onclick="e"' not in joined
    assert 'onclick="x"' not in joined
    assert 'onclick="d"' not in joined
    assert "Obs" in today
    assert "Unverified" in today
    assert "&lt;script&gt;unv()" in today
    assert "&lt;script&gt;delta()" in detail
    for path in (PACKAGE_DIR / "templates").glob("*.html"):
        assert "|safe" not in path.read_text(encoding="utf-8"), path


def test_nav_lists_m4a_pages(data_dir: Path, key_provider: object) -> None:
    open_test_db(data_dir).close()
    client = _client()
    page = client.get("/")
    assert "<title>W.A.T.S.O.N status</title>" in page.text
    assert '<p class="brand">W.A.T.S.O.N</p>' in page.text
    assert 'href="/history"' in page.text
    assert 'href="/projects"' in page.text
    assert 'href="/people"' in page.text
    assert 'href="/today"' in page.text
    assert 'href="/memory"' in page.text
    assert "no working days" in client.get("/history").text
    assert "no projects" in client.get("/projects").text
    assert "no people" in client.get("/people").text
    assert "no working day" in client.get("/today").text
    assert "no claims" in client.get("/memory").text
    assert "capture-state" in page.text


def test_m4a_gets_do_not_write_and_mutations_are_405(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        day_id = _insert_day(conn, date="2026-10-31", state="consolidated")
        frame_id = _insert_frame(conn, day_id)
        segment_id = _insert_segment(conn, day_id, frame_id)
        observation_id = _observation_with_candidates(
            conn,
            segment_id,
            description="Editing the Acme draft",
            projects=["Acme Q4 Proposal"],
            people=["Priya Natarajan"],
        )
        _insert_claim(
            conn,
            observation_id,
            day_id,
            segment_id,
            frame_id,
            kind="activity",
            assertion="observed",
            text="Edited the Acme draft",
        )
        project_id = _insert_entity(conn, name="Acme Q4 Proposal", typ="project")
        person_id = _insert_entity(conn, name="Priya Natarajan", typ="person")
        before = _db_fingerprint(conn)
    finally:
        conn.close()

    client = _client()
    csrf = _csrf(client.get("/today").text)
    paths = (
        "/today",
        "/history",
        "/projects",
        "/people",
        f"/projects/{project_id}",
        f"/people/{person_id}",
        "/api/status",
        "/api/today",
        "/api/history",
        "/api/entities?type=project",
        "/api/entities?type=person",
        f"/api/entities?type=project&id={project_id}",
        "/memory",
        "/api/claims",
    )
    for path in paths:
        page = client.get(path)
        assert page.status_code == 200, path
        for method in ("post", "put", "delete"):
            response = getattr(client, method)(
                path,
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
            )
            assert response.status_code == 405, (method, path)

    conn = open_test_db(data_dir)
    try:
        after = _db_fingerprint(conn)
    finally:
        conn.close()
    assert after == before
    assert sum(count for count, _digest in before.values()) > 0


def test_history_today_and_entity_history_are_scoped(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        older_id = _insert_day(conn, date="2026-10-30", state="consolidated")
        latest_id = _insert_day(conn, date="2026-10-31", state="consolidated")
        older_frame = _insert_frame(conn, older_id)
        latest_frame = _insert_frame(conn, latest_id)
        older_segment = _insert_segment(conn, older_id, older_frame)
        latest_segment = _insert_segment(conn, latest_id, latest_frame)

        older_v1 = _observation_with_candidates(
            conn,
            older_segment,
            description="older-day-only observation",
            projects=["Acme Q4 Proposal"],
            people=[],
        )
        _observation_with_candidates(
            conn,
            older_segment,
            description="older-v2 leaked observation",
            projects=["Acme Q4 Proposal"],
            people=[],
            pipeline_version=99,
        )
        latest_v1 = _observation_with_candidates(
            conn,
            latest_segment,
            description="today-v1 observation",
            projects=["Acme Q4 Proposal"],
            people=[],
        )
        latest_v2 = _observation_with_candidates(
            conn,
            latest_segment,
            description="v2 leaked",
            projects=["Acme Q4 Proposal"],
            people=[],
            pipeline_version=99,
        )
        v1_claim = _insert_claim(
            conn,
            latest_v1,
            latest_id,
            latest_segment,
            latest_frame,
            kind="activity",
            assertion="observed",
            text="v1 entity claim",
        )
        v2_claim = _insert_claim(
            conn,
            latest_v2,
            latest_id,
            latest_segment,
            latest_frame,
            kind="activity",
            assertion="observed",
            text="v2 claim leaked",
            pipeline_version=99,
        )
        _insert_claim(
            conn,
            older_v1,
            older_id,
            older_segment,
            older_frame,
            kind="activity",
            assertion="observed",
            text="older-day claim",
        )
        _insert_summary(conn, latest_id, "latest-v1 summary", [v1_claim])
        _insert_summary(
            conn,
            latest_id,
            "latest-v2 summary leaked",
            [v2_claim],
            pipeline_version=99,
        )
        _insert_summary(conn, older_id, "older-v2-summary leaked", [], pipeline_version=99)
        project_id = _insert_entity(conn, name="Acme Q4 Proposal", typ="project")
        _insert_entity_history(
            conn,
            project_id,
            latest_id,
            delta="v1 delta good",
            claim_ids=[v1_claim],
        )
        _insert_entity_history(
            conn,
            project_id,
            latest_id,
            delta="v2 delta leaked",
            claim_ids=[v2_claim],
            pipeline_version=99,
        )
    finally:
        conn.close()

    client = _client()
    history = client.get("/api/history").json()["days"]
    by_date = {item["date"]: item for item in history}
    assert by_date["2026-10-31"]["observation_count"] == 1
    assert by_date["2026-10-31"]["has_summary"] is True
    assert by_date["2026-10-30"]["observation_count"] == 1
    assert by_date["2026-10-30"]["has_summary"] is False
    history_page = client.get("/history").text
    assert "older-v2-summary leaked" not in history_page
    assert ">yes<" in history_page
    assert ">no<" in history_page

    today = client.get("/today")
    assert today.status_code == 200
    assert "today-v1 observation" in today.text
    assert "v2 leaked" not in today.text
    assert "older-day-only observation" not in today.text
    assert "older-v2 leaked observation" not in today.text
    assert client.get("/api/today").json()["date"] == "2026-10-31"

    detail = client.get(f"/projects/{project_id}")
    assert detail.status_code == 200
    assert "v1 entity claim" in detail.text
    assert "v1 delta good" in detail.text
    assert "v2 claim leaked" not in detail.text
    assert "v2 delta leaked" not in detail.text


def test_entity_claims_match_aliases(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        day_id = _insert_day(conn)
        frame_id = _insert_frame(conn, day_id)
        segment_id = _insert_segment(conn, day_id, frame_id)
        observation_id = _observation_with_candidates(
            conn,
            segment_id,
            description="Working the short name",
            projects=["Acme"],
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
            text="alias-matched claim",
        )
        project_id = _insert_entity(conn, name="Canonical Corp", alias="Acme", typ="project")
    finally:
        conn.close()
    page = _client().get(f"/projects/{project_id}")
    assert page.status_code == 200
    assert "alias-matched claim" in page.text
    assert claim_id in page.text


def test_people_entries_link_to_people_not_projects(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        project_id = _insert_entity(conn, name="Acme Q4 Proposal", typ="project")
        person_id = _insert_entity(conn, name="Priya Natarajan", typ="person")
    finally:
        conn.close()
    client = _client()
    people = client.get("/people")
    assert people.status_code == 200
    assert f'href="/people/{person_id}"' in people.text
    assert f'href="/projects/{person_id}"' not in people.text
    projects = client.get("/projects")
    assert f'href="/projects/{project_id}"' in projects.text
    assert f'href="/people/{project_id}"' not in projects.text


def test_api_entities_rejects_unknown_type(data_dir: Path, key_provider: object) -> None:
    open_test_db(data_dir).close()
    response = _client().get("/api/entities?type=nope")
    assert response.status_code == 400
    assert response.json()["error"] == "type must be project or person"


def test_provisional_flag_is_visible(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        project_id = _insert_entity(conn, name="Draft Acme", typ="project", provisional=1)
    finally:
        conn.close()
    client = _client()
    listing = client.get("/projects")
    assert listing.status_code == 200
    assert "provisional" in listing.text
    detail = client.get(f"/projects/{project_id}")
    assert detail.status_code == 200
    assert "provisional" in detail.text

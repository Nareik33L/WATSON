"""M4b Memory page: claim feedback, entity merge/rename, escaping, scoping."""

from __future__ import annotations

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
from tests.unit.test_dashboard_pages import (
    CSS,
    INSTALL_TOKEN,
    ORIGIN,
    _client,
    _csrf,
    _db_fingerprint,
    _insert_entity,
    _observation_with_candidates,
)
from watson.config import load_config
from watson.dashboard.app import PACKAGE_DIR, create_app


def _seed_memory(conn: object) -> dict[str, str]:
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
    claim_id = _insert_claim(
        conn,
        observation_id,
        day_id,
        segment_id,
        frame_id,
        kind="activity",
        assertion="observed",
        text="Edited the Acme draft",
        snippet="Still editing: Acme proposal appendix",
    )
    project_id = _insert_entity(conn, name="Acme Q4 Proposal", alias="Acme", typ="project")
    other_id = _insert_entity(conn, name="Ghost Deal", typ="project")
    person_id = _insert_entity(conn, name="Priya Natarajan", typ="person")
    return {
        "day_id": day_id,
        "claim_id": claim_id,
        "project_id": project_id,
        "other_id": other_id,
        "person_id": person_id,
    }


def _post_feedback(
    client: TestClient,
    claim_id: str,
    action: str,
    *,
    new_text: str | None = None,
    csrf: str | None = None,
) -> object:
    token = csrf or _csrf(client.get("/memory").text)
    data: dict[str, str] = {"csrf_token": token, "action": action}
    if new_text is not None:
        data["new_text"] = new_text
    return client.post(
        f"/claims/{claim_id}/feedback",
        headers={"Origin": ORIGIN},
        data=data,
        follow_redirects=False,
    )


def test_memory_requires_auth() -> None:
    app = create_app(token=INSTALL_TOKEN, config=load_config(), port=8000)
    client = TestClient(app, base_url=ORIGIN)
    page = client.get("/memory", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/auth"
    api = client.get("/api/claims")
    assert api.status_code == 401


def test_memory_lists_claims_with_quotes_and_actions(
    data_dir: Path, key_provider: object
) -> None:
    conn = open_test_db(data_dir)
    try:
        seeded = _seed_memory(conn)
    finally:
        conn.close()
    client = _client()
    page = client.get("/memory")
    assert page.status_code == 200
    assert "Edited the Acme draft" in page.text
    assert "observed" in page.text
    assert "Still editing: Acme proposal appendix" in page.text
    assert f'id="claim-{seeded["claim_id"]}"' in page.text
    assert "Confirm" in page.text
    assert "Reject" in page.text
    assert "not work" in page.text
    assert "Correct" in page.text
    assert 'href="/memory"' in client.get("/").text
    payload = client.get("/api/claims").json()
    assert payload["date"] == "2026-10-31"
    assert payload["claims"][0]["text"] == "Edited the Acme draft"
    assert payload["claims"][0]["quotes"] == ["Still editing: Acme proposal appendix"]
    one = client.get(f"/api/claims?id={seeded['claim_id']}")
    assert one.status_code == 200
    assert one.json()["id"] == seeded["claim_id"]
    missing = client.get("/api/claims?id=no-such-claim")
    assert missing.status_code == 404


def test_confirm_correct_reject_not_work_via_forms(
    data_dir: Path, key_provider: object
) -> None:
    conn = open_test_db(data_dir)
    try:
        seeded = _seed_memory(conn)
        claim_id = seeded["claim_id"]
        evidence_before = [
            tuple(row) for row in conn.execute("SELECT * FROM claim_evidence ORDER BY id")
        ]
        original = str(
            conn.execute("SELECT text FROM claims WHERE id = ?", (claim_id,)).fetchone()[0]
        )
    finally:
        conn.close()
    client = _client()
    confirm = _post_feedback(client, claim_id, "confirm")
    assert confirm.status_code == 303
    assert "/memory" in confirm.headers["location"]
    page = client.get("/memory")
    assert "user_confirmed" in page.text
    assert "Edited the Acme draft" in page.text
    correct = _post_feedback(client, claim_id, "correct", new_text="Shipped the Acme draft")
    assert correct.status_code == 303
    page = client.get("/memory")
    assert "user_corrected" in page.text
    assert "Shipped the Acme draft" in page.text
    assert "original" in page.text
    assert "Edited the Acme draft" in page.text
    reject = _post_feedback(client, claim_id, "reject")
    assert reject.status_code == 303
    assert "user_rejected" in client.get("/memory").text
    not_work = _post_feedback(client, claim_id, "not_work")
    assert not_work.status_code == 303
    assert "not_work" in client.get("/api/claims").json()["claims"][0]["action"]
    conn = open_test_db(data_dir)
    try:
        stored = str(
            conn.execute("SELECT text FROM claims WHERE id = ?", (claim_id,)).fetchone()[0]
        )
        assert stored == original
        evidence_after = [
            tuple(row) for row in conn.execute("SELECT * FROM claim_evidence ORDER BY id")
        ]
        assert evidence_after == evidence_before
        assert int(conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]) == 4
        actions = [
            str(row[0])
            for row in conn.execute("SELECT action FROM corrections ORDER BY created_at, id")
        ]
        assert actions == ["confirm", "correct", "reject", "not_work"]
    finally:
        conn.close()


def test_json_feedback_and_blank_correct(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        seeded = _seed_memory(conn)
    finally:
        conn.close()
    client = _client()
    csrf = _csrf(client.get("/memory").text)
    blank = client.post(
        f"/api/claims/{seeded['claim_id']}/feedback",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        json={"action": "correct", "new_text": "  "},
    )
    assert blank.status_code == 400
    missing = client.post(
        "/api/claims/no-such-claim/feedback",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        json={"action": "confirm"},
    )
    assert missing.status_code == 404
    ok = client.post(
        f"/api/claims/{seeded['claim_id']}/feedback",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        json={"action": "correct", "new_text": "Fixed wording"},
    )
    assert ok.status_code == 200
    assert ok.json()["ok"] is True
    assert client.get("/api/claims").json()["claims"][0]["text"] == "Fixed wording"


def test_feedback_requires_csrf(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        seeded = _seed_memory(conn)
    finally:
        conn.close()
    client = _client()
    missing = client.post(f"/claims/{seeded['claim_id']}/feedback", data={"action": "confirm"})
    assert missing.status_code == 400
    no_csrf = client.post(
        f"/claims/{seeded['claim_id']}/feedback",
        headers={"Origin": ORIGIN},
        data={"action": "confirm"},
    )
    assert no_csrf.status_code == 403


def test_memory_get_does_not_write(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        _seed_memory(conn)
        before = _db_fingerprint(conn)
    finally:
        conn.close()
    client = _client()
    csrf = _csrf(client.get("/memory").text)
    for path in ("/memory", "/api/claims"):
        assert client.get(path).status_code == 200
        for method in ("put", "delete"):
            response = getattr(client, method)(
                path, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
            )
            assert response.status_code == 405, (method, path)
    conn = open_test_db(data_dir)
    try:
        after = _db_fingerprint(conn)
    finally:
        conn.close()
    assert after == before


def test_memory_is_scoped_by_day_and_pipeline_version(
    data_dir: Path, key_provider: object
) -> None:
    conn = open_test_db(data_dir)
    try:
        older = _insert_day(conn, date="2026-10-30", state="consolidated")
        latest = _insert_day(conn, date="2026-10-31", state="consolidated")
        older_frame = _insert_frame(conn, older)
        latest_frame = _insert_frame(conn, latest)
        older_segment = _insert_segment(conn, older, older_frame)
        latest_segment = _insert_segment(conn, latest, latest_frame)
        older_obs = _insert_observation(conn, older_segment, description="older-day")
        latest_v1 = _insert_observation(conn, latest_segment, description="today-v1")
        latest_v2 = _insert_observation(
            conn, latest_segment, description="today-v2", pipeline_version=2
        )
        older_claim = _insert_claim(
            conn,
            older_obs,
            older,
            older_segment,
            older_frame,
            kind="activity",
            assertion="observed",
            text="older-day claim",
        )
        v1_claim = _insert_claim(
            conn,
            latest_v1,
            latest,
            latest_segment,
            latest_frame,
            kind="activity",
            assertion="observed",
            text="today-v1 claim",
        )
        v2_claim = _insert_claim(
            conn,
            latest_v2,
            latest,
            latest_segment,
            latest_frame,
            kind="activity",
            assertion="observed",
            text="today-v2 leaked",
            pipeline_version=2,
        )
    finally:
        conn.close()
    client = _client()
    today = client.get("/memory")
    assert "today-v1 claim" in today.text
    assert "today-v2 leaked" not in today.text
    assert "older-day claim" not in today.text
    older_page = client.get("/memory?date=2026-10-30")
    assert "older-day claim" in older_page.text
    assert "today-v1 claim" not in older_page.text
    csrf = _csrf(today.text)
    client.post(
        f"/claims/{v1_claim}/feedback",
        headers={"Origin": ORIGIN},
        data={"csrf_token": csrf, "action": "correct", "new_text": "today-v1 corrected"},
        follow_redirects=False,
    )
    assert "today-v1 corrected" in client.get("/memory").text
    assert "today-v1 corrected" not in client.get("/memory?date=2026-10-30").text
    assert "older-day claim" in client.get("/memory?date=2026-10-30").text
    assert v2_claim
    assert older_claim


def test_hostile_correction_text_is_escaped(data_dir: Path, key_provider: object) -> None:
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
            kind="activity",
            assertion="observed",
            text='Claim <script>c()</script> onclick="e"',
            snippet='Quote <script>q()</script> and "quotes"',
        )
        project_id = _insert_entity(
            conn, name='Acme <img src=x onerror="q">', typ="project"
        )
        _insert_entity(conn, name="Safe Deal", typ="project")
    finally:
        conn.close()
    client = _client()
    csrf = _csrf(client.get("/memory").text)
    client.post(
        f"/claims/{claim_id}/feedback",
        headers={"Origin": ORIGIN},
        data={
            "csrf_token": csrf,
            "action": "correct",
            "new_text": 'Fixed <script>pwn()</script> onclick="x"',
        },
        follow_redirects=False,
    )
    bodies = [
        client.get("/memory").text,
        client.get(f"/projects/{project_id}").text,
    ]
    joined = "\n".join(bodies)
    assert "<script>" not in joined
    assert "&lt;script&gt;" in joined
    assert 'onclick="x"' not in joined
    assert 'onclick="e"' not in joined
    for path in (PACKAGE_DIR / "templates").glob("*.html"):
        assert "|safe" not in path.read_text(encoding="utf-8"), path
    assert "flex-wrap: wrap" in CSS


def test_rename_and_merge_entities(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        seeded = _seed_memory(conn)
        project_id = seeded["project_id"]
        other_id = seeded["other_id"]
    finally:
        conn.close()
    client = _client()
    csrf = _csrf(client.get(f"/projects/{project_id}").text)
    rename = client.post(
        f"/entities/{project_id}/rename",
        headers={"Origin": ORIGIN},
        data={"csrf_token": csrf, "new_text": "Acme Deal"},
        follow_redirects=False,
    )
    assert rename.status_code == 303
    assert f"/projects/{project_id}" in rename.headers["location"]
    page = client.get(f"/projects/{project_id}")
    assert "Acme Deal" in page.text
    assert "Acme Q4 Proposal" in page.text
    merge = client.post(
        f"/entities/{other_id}/merge",
        headers={"Origin": ORIGIN},
        data={"csrf_token": csrf, "into_id": project_id},
        follow_redirects=False,
    )
    assert merge.status_code == 303
    assert f"/projects/{project_id}" in merge.headers["location"]
    listing = client.get("/projects")
    assert f'href="/projects/{other_id}"' not in listing.text
    assert f'href="/projects/{project_id}"' in listing.text
    assert "Acme Deal" in listing.text
    assert "Ghost Deal" in listing.text
    detail = client.get(f"/projects/{project_id}")
    assert "Ghost Deal" in detail.text
    missing = client.get(f"/projects/{other_id}")
    assert missing.status_code == 404
    conn = open_test_db(data_dir)
    try:
        src = conn.execute(
            "SELECT canonical_name, merged_into_id FROM entities WHERE id = ?", (other_id,)
        ).fetchone()
        assert str(src[0]) == "Ghost Deal"
        assert str(src[1]) == project_id
        actions = {
            str(row[0]) for row in conn.execute("SELECT action FROM corrections")
        }
        assert actions == {"correct", "merge"}
    finally:
        conn.close()
    json_csrf = _csrf(client.get("/people").text)
    person = seeded["person_id"]
    api = client.post(
        f"/api/entities/{person}/rename",
        headers={"Origin": ORIGIN, "X-CSRF-Token": json_csrf},
        json={"new_text": "Priya N."},
    )
    assert api.status_code == 200
    assert "Priya N." in client.get(f"/people/{person}").text


def test_empty_memory_and_css_wrap(data_dir: Path, key_provider: object) -> None:
    open_test_db(data_dir).close()
    client = _client()
    page = client.get("/memory")
    assert page.status_code == 200
    assert "no claims" in page.text
    assert "flex-wrap: wrap" in CSS
    assert ".claim-actions" in CSS

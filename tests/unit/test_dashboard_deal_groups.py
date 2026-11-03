"""Today / History deal groups: titles, cited slots, channels, none (plan §8).

No-network rendering. Duplicate titles stay two cards. Empty days stay empty.
Skipped excluded-app frames never appear. Nothing new is logged.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.replay_support import open_test_db
from tests.unit.test_consolidation import (
    _insert_claim,
    _insert_day,
    _insert_frame,
    _insert_observation,
    _insert_segment,
)
from tests.unit.test_dashboard_pages import INSTALL_TOKEN, ORIGIN, _client
from watson.config import load_config
from watson.dashboard.app import create_app
from watson.memory.ids import new_id
from watson.pipeline.deal_groups import collect_deal_groups, segment_channel
from watson.pipeline.frames import PIPELINE_VERSION
from watson.privacy.network import runtime_network_guard

EXCLUDED_TITLE = "Personal vault"
FERRY_TITLE = "Harbor ferry news"
ACME_TITLE = "Acme Q4 proposal"


def test_segment_channel_classifies_fixture_apps() -> None:
    assert segment_channel("Lumen Mail", "Inbox") == "email"
    assert segment_channel("Lumen Relay", "Priya / Acme") == "call"
    assert segment_channel("Lumen Quill", "Q4 deck") == "doc"
    assert segment_channel("Lumen Compass", "Acme CRM") == "other"


def _frame(
    conn: object,
    day_id: str,
    *,
    app_name: str,
    window_title: str,
    captured_at: str,
    state: str = "extracted",
) -> str:
    frame_id = _insert_frame(conn, day_id, captured_at=captured_at, state=state)
    conn.execute(
        """
        UPDATE frames
        SET app_name = ?, window_title = ?, bundle_id = ?
        WHERE id = ?
        """,
        (app_name, window_title, "com.lumen.test", frame_id),
    )
    conn.commit()
    return frame_id


def _insert_deal(
    conn: object,
    day_id: str,
    title: str,
    *,
    stamp: str = "2026-10-31T18:00:00Z",
) -> str:
    deal_id = new_id()
    conn.execute(
        """
        INSERT INTO deals (
            id, created_at, updated_at, working_day_id, title, summary,
            observation_ids, prompt_version, pipeline_version
        ) VALUES (?, ?, ?, ?, ?, ?, '[]', 'clustering/v2', ?)
        """,
        (deal_id, stamp, stamp, day_id, title, title, PIPELINE_VERSION),
    )
    conn.commit()
    return deal_id


def _assign(conn: object, segment_id: str, deal_id: str | None) -> None:
    conn.execute("UPDATE segments SET deal_id = ? WHERE id = ?", (deal_id, segment_id))
    conn.commit()


def _insert_deal_summary(
    conn: object,
    day_id: str,
    narrative: str,
    claim_ids: list[str],
    *,
    deals: list[dict[str, str]],
    slots: list[dict[str, object]],
) -> None:
    stamp = "2026-10-31T18:00:00Z"
    conn.execute(
        """
        INSERT INTO daily_summaries (
            id, created_at, updated_at, working_day_id, narrative_md,
            sections_json, claim_ids, prompt_version, pipeline_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'daily_summary/v2', ?)
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
                    "unverified": [],
                    "claim_ids": claim_ids,
                    "deals": deals,
                    "slots": slots,
                }
            ),
            json.dumps(claim_ids),
            PIPELINE_VERSION,
        ),
    )
    conn.commit()


def _seed_multichannel(conn: object) -> dict[str, str]:
    day_id = _insert_day(conn, date="2026-10-31", state="consolidated")
    mail_f = _frame(
        conn,
        day_id,
        app_name="Lumen Mail",
        window_title="Priya / pricing",
        captured_at="2026-10-31T09:00:00Z",
    )
    call_f = _frame(
        conn,
        day_id,
        app_name="Lumen Relay",
        window_title="Acme pricing call",
        captured_at="2026-10-31T10:00:00Z",
    )
    doc_f = _frame(
        conn,
        day_id,
        app_name="Lumen Quill",
        window_title="Q4 deck",
        captured_at="2026-10-31T11:00:00Z",
    )
    none_f = _frame(
        conn,
        day_id,
        app_name="Cedar Daily",
        window_title=FERRY_TITLE,
        captured_at="2026-10-31T12:00:00Z",
    )
    mail = _insert_segment(
        conn, day_id, mail_f, started_at="2026-10-31T09:00:00Z", merged="pricing email"
    )
    call = _insert_segment(
        conn, day_id, call_f, started_at="2026-10-31T10:00:00Z", merged="pricing call"
    )
    doc = _insert_segment(conn, day_id, doc_f, started_at="2026-10-31T11:00:00Z", merged="q4 deck")
    noise = _insert_segment(
        conn, day_id, none_f, started_at="2026-10-31T12:00:00Z", merged="ferry news"
    )
    mail_obs = _insert_observation(conn, mail, description="Emailing Priya about pricing")
    call_obs = _insert_observation(conn, call, description="Pricing call with Priya")
    doc_obs = _insert_observation(conn, doc, description="Editing the Q4 deck")
    _insert_observation(conn, noise, description="Reading civic news")
    sent = _insert_claim(
        conn,
        mail_obs,
        day_id,
        mail,
        mail_f,
        kind="activity",
        assertion="observed",
        text="Sent the pricing pack",
        snippet="pricing email body",
    )
    who = _insert_claim(
        conn,
        call_obs,
        day_id,
        call,
        call_f,
        kind="person_interaction",
        assertion="observed",
        text="Priya joined the call",
        snippet="call overlay",
    )
    nxt = _insert_claim(
        conn,
        doc_obs,
        day_id,
        doc,
        doc_f,
        kind="commitment",
        assertion="observed",
        text="Kieran will send revised pricing",
        snippet="deck comment",
    )
    deal_id = _insert_deal(conn, day_id, ACME_TITLE)
    _assign(conn, mail, deal_id)
    _assign(conn, call, deal_id)
    _assign(conn, doc, deal_id)
    narrative = (
        f"## {ACME_TITLE}\n\n"
        f"What happened: Sent the pricing pack [c:{sent}].\n\n"
        f"Who involved: Priya joined the call [c:{who}].\n\n"
        f"Next step: Kieran will send revised pricing [c:{nxt}]."
    )
    _insert_deal_summary(
        conn,
        day_id,
        narrative,
        [sent, who, nxt],
        deals=[{"id": deal_id, "title": ACME_TITLE}],
        slots=[
            {"slot": "what_happened", "claim_ids": [sent], "deal_id": deal_id},
            {"slot": "who_involved", "claim_ids": [who], "deal_id": deal_id},
            {"slot": "next_step", "claim_ids": [nxt], "deal_id": deal_id},
        ],
    )
    return {
        "day_id": day_id,
        "deal_id": deal_id,
        "mail": mail,
        "call": call,
        "doc": doc,
        "noise": noise,
        "sent": sent,
    }


def _article(html: str, deal_id: str) -> str:
    marker = f'id="deal-{deal_id}"'
    start = html.index(marker)
    rest = html[start:]
    end = rest.find("</article>")
    assert end != -1
    return rest[: end + len("</article>")]


def test_today_and_history_render_cited_deal_groups(
    data_dir: Path, key_provider: object, caplog: pytest.LogCaptureFixture
) -> None:
    conn = open_test_db(data_dir)
    try:
        seeded = _seed_multichannel(conn)
        groups = collect_deal_groups(conn, working_day_id=seeded["day_id"])
        assert len(groups.deals) == 1
        card = groups.deals[0]
        assert card.title == ACME_TITLE
        assert [item.id for item in card.emails] == [seeded["mail"]]
        assert [item.id for item in card.calls] == [seeded["call"]]
        assert [item.id for item in card.docs] == [seeded["doc"]]
        assert groups.none_segments[0].id == seeded["noise"]
        assert groups.none_segments[0].window_title == FERRY_TITLE
        what = card.slots[0]
        assert what.slot == "what_happened"
        assert any(span.segment_id == seeded["mail"] for span in what.spans)
    finally:
        conn.close()

    caplog.set_level(logging.DEBUG)
    with runtime_network_guard():
        client = _client()
        today = client.get("/today")
        history = client.get("/history")
        days = client.get("/days?date=2026-10-31")
        today_api = client.get("/api/today")
        history_api = client.get("/api/history")
    assert today.status_code == 200
    assert history.status_code == 200
    assert days.status_code == 200
    for page in (today, history, days):
        body = page.text
        article = _article(body, seeded["deal_id"])
        assert ACME_TITLE in article
        assert "What happened" in article
        assert "Who involved" in article
        assert "Next step" in article
        assert "Sent the pricing pack" in article
        assert f'href="#segment-{seeded["mail"]}"' in article
        assert f'id="segment-{seeded["mail"]}"' in article
        assert f'id="segment-{seeded["call"]}"' in article
        assert f'id="segment-{seeded["doc"]}"' in article
        assert "Emails" in article
        assert "Calls" in article
        assert "Docs" in article
        assert "Priya / pricing" in article
        assert "Acme pricing call" in article
        assert "Q4 deck" in article
        assert FERRY_TITLE not in article
        assert "No deal" in body
        assert FERRY_TITLE in body
        assert "snippet" not in body
        assert "merged_text" not in body
        assert "ocr" not in body.lower()
    payload = today_api.json()
    assert payload["deals"][0]["title"] == ACME_TITLE
    assert payload["none_segments"][0]["window_title"] == FERRY_TITLE
    assert "merged_text" not in str(payload)
    hist = history_api.json()["days"][0]
    assert hist["deals"][0]["title"] == ACME_TITLE
    assert hist["none_count"] == 1
    assert FERRY_TITLE not in caplog.text
    assert "Sent the pricing pack" not in caplog.text
    assert "pricing email body" not in caplog.text


def test_duplicate_title_pair_renders_two_cards(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        day_id = _insert_day(conn, date="2026-10-31", state="consolidated")
        first_f = _frame(
            conn,
            day_id,
            app_name="Lumen Mail",
            window_title="Northwind east",
            captured_at="2026-10-31T09:00:00Z",
        )
        second_f = _frame(
            conn,
            day_id,
            app_name="Lumen Mail",
            window_title="Northwind west",
            captured_at="2026-10-31T10:00:00Z",
        )
        first_seg = _insert_segment(conn, day_id, first_f, started_at="2026-10-31T09:00:00Z")
        second_seg = _insert_segment(conn, day_id, second_f, started_at="2026-10-31T10:00:00Z")
        first_obs = _insert_observation(conn, first_seg, description="East renewal mail")
        second_obs = _insert_observation(conn, second_seg, description="West renewal mail")
        first_claim = _insert_claim(
            conn,
            first_obs,
            day_id,
            first_seg,
            first_f,
            kind="activity",
            assertion="observed",
            text="Mailed the east renewal",
        )
        second_claim = _insert_claim(
            conn,
            second_obs,
            day_id,
            second_seg,
            second_f,
            kind="activity",
            assertion="observed",
            text="Mailed the west renewal",
        )
        title = "Northwind renewal"
        first_deal = _insert_deal(conn, day_id, title, stamp="2026-10-31T18:00:00Z")
        second_deal = _insert_deal(conn, day_id, title, stamp="2026-10-31T18:00:01Z")
        _assign(conn, first_seg, first_deal)
        _assign(conn, second_seg, second_deal)
        narrative = (
            f"## {title}\n\nWhat happened: Mailed the east renewal [c:{first_claim}].\n\n"
            f"## {title}\n\nWhat happened: Mailed the west renewal [c:{second_claim}]."
        )
        _insert_deal_summary(
            conn,
            day_id,
            narrative,
            [first_claim, second_claim],
            deals=[
                {"id": first_deal, "title": title},
                {"id": second_deal, "title": title},
            ],
            slots=[
                {
                    "slot": "what_happened",
                    "claim_ids": [first_claim],
                    "deal_id": first_deal,
                },
                {
                    "slot": "what_happened",
                    "claim_ids": [second_claim],
                    "deal_id": second_deal,
                },
            ],
        )
        ids = (first_deal, second_deal)
    finally:
        conn.close()

    with runtime_network_guard():
        client = _client()
        today = client.get("/today")
        history = client.get("/history")
    for page in (today, history):
        assert page.text.count(f">{title}<") == 2
        first_html = _article(page.text, ids[0])
        second_html = _article(page.text, ids[1])
        assert first_html != second_html
        assert "Northwind east" in first_html
        assert "Northwind west" not in first_html
        assert "Northwind west" in second_html
        assert "Northwind east" not in second_html
        assert "Mailed the east renewal" in first_html
        assert "Mailed the west renewal" in second_html


def test_empty_day_renders_no_deals(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        _insert_day(conn, date="2026-10-31", state="open")
    finally:
        conn.close()

    with runtime_network_guard():
        client = _client()
        today = client.get("/today")
        history = client.get("/history")
        days = client.get("/days?date=2026-10-31")
        today_api = client.get("/api/today")
        history_api = client.get("/api/history")
    assert today.status_code == 200
    assert "no working day" not in today.text
    assert "no segments" in today.text
    assert "no deals" in today.text
    assert "No deal" not in today.text
    assert "no deals" in history.text
    assert "No deal" not in history.text
    assert "no deals" in days.text
    assert today_api.json()["deals"] == []
    assert today_api.json()["none_segments"] == []
    assert history_api.json()["days"][0]["deals"] == []
    assert history_api.json()["days"][0]["none_count"] == 0


def test_excluded_app_content_stays_off_deal_groups(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        seeded = _seed_multichannel(conn)
        bait = _frame(
            conn,
            seeded["day_id"],
            app_name="Passwords",
            window_title=EXCLUDED_TITLE,
            captured_at="2026-10-31T13:00:00Z",
            state="skipped",
        )
        bait_seg = _insert_segment(
            conn,
            seeded["day_id"],
            bait,
            started_at="2026-10-31T13:00:00Z",
            merged="vault secret",
        )
        _assign(conn, bait_seg, seeded["deal_id"])
        conn.execute(
            "UPDATE frame_text SET text_redacted = ? WHERE frame_id = ?",
            ("PLANTED_EXCLUDED_OCR_MUST_NEVER_RENDER", bait),
        )
        conn.commit()
    finally:
        conn.close()

    with runtime_network_guard():
        client = _client()
        today = client.get("/today")
        history = client.get("/history")
        payload = client.get("/api/today").json()
    for body in (today.text, history.text, str(payload)):
        assert EXCLUDED_TITLE not in body
        assert "Passwords" not in body
        assert "PLANTED_EXCLUDED_OCR_MUST_NEVER_RENDER" not in body
        assert "vault secret" not in body
        assert f'id="segment-{bait_seg}"' not in body


def test_unauthenticated_deal_pages_stay_closed() -> None:
    app = create_app(token=INSTALL_TOKEN, config=load_config(), port=8000)
    client = TestClient(app, base_url=ORIGIN)
    with runtime_network_guard():
        today = client.get("/today", follow_redirects=False)
        history = client.get("/history", follow_redirects=False)
    assert today.status_code == 303
    assert history.status_code == 303

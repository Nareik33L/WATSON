"""Today deal groups: titles, cited slots, channels, none (plan §8 / M3f).

History is unchanged. No-network rendering. Duplicate titles stay two cards.
Empty days stay empty. Skipped excluded-app frames never appear. Nothing new
is logged.
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
from watson.pipeline.deal_groups import (
    _parse_slot_text,
    collect_deal_groups,
    segment_channel,
)
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
    paragraphs: list[object] | None = None,
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
                    "paragraphs": paragraphs if paragraphs is not None else [narrative],
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


def test_today_renders_cited_deal_groups(
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
    body = today.text
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
    assert f'id="deal-{seeded["deal_id"]}"' not in history.text
    assert f'id="deal-{seeded["deal_id"]}"' not in days.text
    assert "deals" not in history_api.json()["days"][0]
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
    assert today.text.count(f">{title}<") == 2
    first_html = _article(today.text, ids[0])
    second_html = _article(today.text, ids[1])
    assert first_html != second_html
    assert "Northwind east" in first_html
    assert "Northwind west" not in first_html
    assert "Northwind west" in second_html
    assert "Northwind east" not in second_html
    assert "Mailed the east renewal" in first_html
    assert "Mailed the west renewal" in second_html
    assert f'id="deal-{ids[0]}"' not in history.text
    assert f'id="deal-{ids[1]}"' not in history.text


def _slot_blob(card, slot: str = "what_happened") -> str:
    for item in card.slots:
        if item.slot == slot:
            return "".join(span.text for span in item.spans)
    return ""


def test_duplicate_title_same_created_at_missing_block_stays_unswapped(
    data_dir: Path, key_provider: object
) -> None:
    """Same created_at, duplicate titles, first deal has no block: never steal the other card.

    Loops so a UUID-order shuffle cannot hide a title-match or take-first-block mutant.
    """
    conn = open_test_db(data_dir)
    try:
        for round_no in range(20):
            day_id = _insert_day(conn, date=f"2026-09-{(round_no % 28) + 1:02d}")
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
            second_obs = _insert_observation(conn, second_seg, description="West renewal mail")
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
            stamp = "2026-10-31T18:00:00Z"
            if round_no % 2 == 0:
                first_deal = _insert_deal(conn, day_id, title, stamp=stamp)
                second_deal = _insert_deal(conn, day_id, title, stamp=stamp)
            else:
                second_deal = _insert_deal(conn, day_id, title, stamp=stamp)
                first_deal = _insert_deal(conn, day_id, title, stamp=stamp)
            _assign(conn, first_seg, first_deal)
            _assign(conn, second_seg, second_deal)
            narrative = f"## {title}\n\nWhat happened: Mailed the west renewal [c:{second_claim}]."
            _insert_deal_summary(
                conn,
                day_id,
                narrative,
                [second_claim],
                deals=[
                    {"id": first_deal, "title": title},
                    {"id": second_deal, "title": title},
                ],
                slots=[
                    {
                        "slot": "what_happened",
                        "claim_ids": [second_claim],
                        "deal_id": second_deal,
                    }
                ],
            )
            groups = collect_deal_groups(conn, working_day_id=day_id)
            assert [card.id for card in groups.deals] == [first_deal, second_deal], round_no
            by_id = {card.id: card for card in groups.deals}
            first_blob = _slot_blob(by_id[first_deal])
            second_blob = _slot_blob(by_id[second_deal])
            assert "west renewal" not in first_blob, round_no
            assert "east" not in first_blob, round_no
            assert second_claim not in first_blob, round_no
            assert second_seg not in {span.segment_id for span in by_id[first_deal].slots[0].spans}
            assert "west renewal" in second_blob, round_no
            assert any(
                span.segment_id == second_seg for span in by_id[second_deal].slots[0].spans
            ), round_no
            assert by_id[first_deal].emails[0].id == first_seg
            assert by_id[second_deal].emails[0].id == second_seg
    finally:
        conn.close()


def _seed_claimless_and_fallback(conn: object, *, tagged: bool) -> dict[str, str]:
    """Three deals: slotted model block, claimless, fallback-only (no slot)."""
    day_id = _insert_day(conn, date="2026-10-31", state="consolidated")
    acme_f = _frame(
        conn,
        day_id,
        app_name="Lumen Mail",
        window_title="Acme pricing",
        captured_at="2026-10-31T09:00:00Z",
    )
    globex_f = _frame(
        conn,
        day_id,
        app_name="Lumen Mail",
        window_title="Globex intro",
        captured_at="2026-10-31T10:00:00Z",
    )
    initech_f = _frame(
        conn,
        day_id,
        app_name="Lumen Mail",
        window_title="Initech renewal",
        captured_at="2026-10-31T11:00:00Z",
    )
    acme_seg = _insert_segment(conn, day_id, acme_f, started_at="2026-10-31T09:00:00Z")
    globex_seg = _insert_segment(conn, day_id, globex_f, started_at="2026-10-31T10:00:00Z")
    initech_seg = _insert_segment(conn, day_id, initech_f, started_at="2026-10-31T11:00:00Z")
    acme_obs = _insert_observation(conn, acme_seg, description="Emailing Acme")
    initech_obs = _insert_observation(conn, initech_seg, description="Emailing Initech")
    sent = _insert_claim(
        conn,
        acme_obs,
        day_id,
        acme_seg,
        acme_f,
        kind="activity",
        assertion="observed",
        text="Sent the pricing pack",
    )
    fallback = _insert_claim(
        conn,
        initech_obs,
        day_id,
        initech_seg,
        initech_f,
        kind="activity",
        assertion="observed",
        text="Drafted the fallback cite",
    )
    stamp = "2026-10-31T18:00:00Z"
    acme_deal = _insert_deal(conn, day_id, "Acme Q4", stamp=stamp)
    globex_deal = _insert_deal(conn, day_id, "Globex intro", stamp=stamp)
    initech_deal = _insert_deal(conn, day_id, "Initech renewal", stamp=stamp)
    _assign(conn, acme_seg, acme_deal)
    _assign(conn, globex_seg, globex_deal)
    _assign(conn, initech_seg, initech_deal)
    acme_block = f"## Acme Q4\n\nWhat happened: Sent the pricing pack [c:{sent}]."
    fallback_block = f"## Initech renewal\n\nDrafted the fallback cite [c:{fallback}]."
    narrative = f"{acme_block}\n\n{fallback_block}"
    paragraphs: list[object] = (
        [
            {"deal_id": acme_deal, "text": acme_block},
            {"deal_id": initech_deal, "text": fallback_block},
        ]
        if tagged
        else [narrative]
    )
    _insert_deal_summary(
        conn,
        day_id,
        narrative,
        [sent, fallback],
        deals=[
            {"id": acme_deal, "title": "Acme Q4"},
            {"id": globex_deal, "title": "Globex intro"},
            {"id": initech_deal, "title": "Initech renewal"},
        ],
        slots=[{"slot": "what_happened", "claim_ids": [sent], "deal_id": acme_deal}],
        paragraphs=paragraphs,
    )
    return {
        "day_id": day_id,
        "acme": acme_deal,
        "globex": globex_deal,
        "initech": initech_deal,
        "sent": sent,
        "fallback": fallback,
        "acme_seg": acme_seg,
        "initech_seg": initech_seg,
    }


def _assert_claimless_and_fallback(groups, seeded: dict[str, str]) -> None:
    by_id = {card.id: card for card in groups.deals}
    assert set(by_id) == {seeded["acme"], seeded["globex"], seeded["initech"]}
    acme = _slot_blob(by_id[seeded["acme"]])
    globex = _slot_blob(by_id[seeded["globex"]])
    initech = _slot_blob(by_id[seeded["initech"]])
    assert "Sent the pricing pack" in acme
    assert seeded["sent"] in acme
    assert "fallback cite" not in acme
    assert globex in {"", "—"}
    assert "Sent the pricing pack" not in globex
    assert "fallback cite" not in globex
    assert "Drafted the fallback cite" in initech
    assert seeded["fallback"] in initech
    assert "pricing pack" not in initech
    initech_cites = {span.segment_id for span in by_id[seeded["initech"]].slots[0].spans}
    assert seeded["initech_seg"] in initech_cites


def test_claimless_and_fallback_only_keeps_cited_summary(
    data_dir: Path, key_provider: object
) -> None:
    conn = open_test_db(data_dir)
    try:
        seeded = _seed_claimless_and_fallback(conn, tagged=True)
        groups = collect_deal_groups(conn, working_day_id=seeded["day_id"])
        _assert_claimless_and_fallback(groups, seeded)
    finally:
        conn.close()


def test_legacy_rows_without_paragraph_deal_id_still_render(
    data_dir: Path, key_provider: object
) -> None:
    conn = open_test_db(data_dir)
    try:
        seeded = _seed_claimless_and_fallback(conn, tagged=False)
        groups = collect_deal_groups(conn, working_day_id=seeded["day_id"])
        _assert_claimless_and_fallback(groups, seeded)
    finally:
        conn.close()


def test_slot_prefixes_keep_who_and_next_apart() -> None:
    parsed = _parse_slot_text(
        "Who involved: Priya Natarajan [c:who].\nNext step: Send revised pricing [c:nxt]."
    )
    assert "Priya" in parsed["who_involved"]
    assert "Send revised pricing" in parsed["next_step"]
    assert "Priya" not in parsed.get("next_step", "")
    assert "Send revised pricing" not in parsed.get("who_involved", "")


def test_unlabeled_fallback_does_not_fill_when_slots_exist() -> None:
    parsed = _parse_slot_text(
        "Preamble leftover\nWhat happened: Sent the pricing pack [c:sent].\n"
        "Note: 1 segment(s) were excluded after understanding failed."
    )
    assert parsed["what_happened"] == "Sent the pricing pack [c:sent]."
    assert "Preamble" not in parsed["what_happened"]
    assert "excluded" not in parsed["what_happened"]


def test_trailing_excluded_note_stays_off_deal_cards(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        seeded = _seed_multichannel(conn)
        conn.execute(
            """
            UPDATE daily_summaries
            SET narrative_md = narrative_md || ?
            WHERE working_day_id = ?
            """,
            (
                "\n\nNote: 1 segment(s) were excluded after understanding failed.",
                seeded["day_id"],
            ),
        )
        conn.commit()
        groups = collect_deal_groups(conn, working_day_id=seeded["day_id"])
        blob = " ".join(_slot_blob(groups.deals[0], slot.slot) for slot in groups.deals[0].slots)
        assert "excluded" not in blob
        assert "Note:" not in blob
    finally:
        conn.close()


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
        today_api = client.get("/api/today")
        history_api = client.get("/api/history")
    assert today.status_code == 200
    assert "no working day" not in today.text
    assert "no segments" in today.text
    assert "no deals" in today.text
    assert "No deal" not in today.text
    assert "no deals" not in history.text
    assert today_api.json()["deals"] == []
    assert today_api.json()["none_segments"] == []
    assert "deals" not in history_api.json()["days"][0]


def test_segments_without_deals_render_none_section(data_dir: Path, key_provider: object) -> None:
    conn = open_test_db(data_dir)
    try:
        day_id = _insert_day(conn, date="2026-10-31", state="consolidated")
        frame = _frame(
            conn,
            day_id,
            app_name="Cedar Daily",
            window_title=FERRY_TITLE,
            captured_at="2026-10-31T12:00:00Z",
        )
        noise = _insert_segment(
            conn, day_id, frame, started_at="2026-10-31T12:00:00Z", merged="ferry news"
        )
        _insert_observation(conn, noise, description="Reading civic news")
        groups = collect_deal_groups(conn, working_day_id=day_id)
        assert groups.deals == ()
        assert [item.id for item in groups.none_segments] == [noise]
    finally:
        conn.close()

    with runtime_network_guard():
        client = _client()
        today = client.get("/today")
        payload = client.get("/api/today").json()
    assert today.status_code == 200
    assert "no deals" in today.text
    assert "No deal" in today.text
    assert FERRY_TITLE in today.text
    assert payload["deals"] == []
    assert payload["none_segments"][0]["window_title"] == FERRY_TITLE
    assert payload["none_segments"][0]["id"] == noise


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
        payload = client.get("/api/today").json()
    for body in (today.text, str(payload)):
        assert EXCLUDED_TITLE not in body
        assert "Passwords" not in body
        assert "PLANTED_EXCLUDED_OCR_MUST_NEVER_RENDER" not in body
        assert "vault secret" not in body
        assert f'id="segment-{bait_seg}"' not in body


def test_unauthenticated_today_stays_closed() -> None:
    app = create_app(token=INSTALL_TOKEN, config=load_config(), port=8000)
    client = TestClient(app, base_url=ORIGIN)
    with runtime_network_guard():
        today = client.get("/today", follow_redirects=False)
    assert today.status_code == 303

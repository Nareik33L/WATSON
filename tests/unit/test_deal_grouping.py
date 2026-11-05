"""M3f deal grouping and per-deal daily summary (plan §3.7 / D3 / D8)."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest

from tests.unit.test_consolidation import (
    NOW,
    _insert_claim,
    _insert_entity,
    _insert_frame,
    _insert_observation,
    _insert_segment,
    _open,
    _runtime,
    _seed_day,
)
from watson.intelligence.prompts import load_prompt
from watson.intelligence.runtime import FakeRuntime
from watson.intelligence.runtime.types import OutputParseError
from watson.intelligence.schemas.consolidation import ClusteringOutput, SummaryParagraph
from watson.pipeline.consolidation import (
    _summary_deal_sections,
    consolidate_working_day,
    derived_counts,
)
from watson.privacy.logging import configure_logging

_DEAL_RECORDINGS = {
    "complete": [
        {
            "match": "Cluster these observations",
            "response": {
                "deals": [
                    {
                        "title": "Acme Q4 proposal",
                        "summary": "Email, call, and doc about the same opportunity.",
                        "observation_indexes": [1, 2, 3],
                    }
                ]
            },
        },
        {
            "match": "Extract cross-segment",
            "response": {"entity_deltas": [], "decisions": [], "commitments": []},
        },
        {
            "match": "Write the daily narrative",
            "response": {
                "paragraphs": [
                    {
                        "text": "What happened: Sent the pricing pack [c:1].",
                        "claim_ids": ["1"],
                        "slot": "what_happened",
                    },
                    {
                        "text": "Who involved: Priya joined the call [c:2].",
                        "claim_ids": ["2"],
                        "slot": "who_involved",
                    },
                    {
                        "text": "Next step: Kieran will send revised pricing [c:3].",
                        "claim_ids": ["3"],
                        "slot": "next_step",
                    },
                ]
            },
        },
    ]
}


def _recordings_with_summary(paragraphs: list[dict[str, object]]) -> dict[str, object]:
    return {
        "complete": [
            _DEAL_RECORDINGS["complete"][0],
            _DEAL_RECORDINGS["complete"][1],
            {
                "match": "Write the daily narrative",
                "response": {"paragraphs": paragraphs},
            },
        ]
    }


def _seed_channels(conn: object) -> dict[str, str]:
    from tests.unit.test_consolidation import _insert_day

    day_id = _insert_day(conn)
    mail = _insert_frame(conn, day_id, captured_at="2026-10-31T09:00:00Z")
    call = _insert_frame(conn, day_id, captured_at="2026-10-31T10:00:00Z")
    doc = _insert_frame(conn, day_id, captured_at="2026-10-31T11:00:00Z")
    noise = _insert_frame(conn, day_id, captured_at="2026-10-31T12:00:00Z")
    mail_seg = _insert_segment(
        conn, day_id, mail, started_at="2026-10-31T09:00:00Z", merged="Acme pricing email"
    )
    call_seg = _insert_segment(
        conn, day_id, call, started_at="2026-10-31T10:00:00Z", merged="Acme pricing call"
    )
    doc_seg = _insert_segment(
        conn, day_id, doc, started_at="2026-10-31T11:00:00Z", merged="Acme Q4 deck"
    )
    noise_seg = _insert_segment(
        conn, day_id, noise, started_at="2026-10-31T12:00:00Z", merged="Harbor ferry news"
    )
    mail_obs = _insert_observation(
        conn,
        mail_seg,
        description="Emailing Priya about Acme Q4 pricing",
        project_candidates=["Acme Q4 proposal"],
    )
    call_obs = _insert_observation(
        conn,
        call_seg,
        description="Pricing call with Priya on Acme Q4",
        project_candidates=["Acme Q4 proposal"],
    )
    doc_obs = _insert_observation(
        conn,
        doc_seg,
        description="Editing the Acme Q4 deck after the call",
        project_candidates=["Acme Q4 proposal"],
    )
    noise_obs = _insert_observation(
        conn,
        noise_seg,
        description="Reading civic news in Cedar Daily",
        project_candidates=[],
    )
    sent = _insert_claim(
        conn,
        mail_obs,
        day_id,
        mail_seg,
        mail,
        kind="activity",
        assertion="observed",
        text="Sent the pricing pack",
        created_at="2026-10-31T09:00:01Z",
    )
    who = _insert_claim(
        conn,
        call_obs,
        day_id,
        call_seg,
        call,
        kind="person_interaction",
        assertion="observed",
        text="Priya joined the call",
        created_at="2026-10-31T10:00:01Z",
    )
    nxt = _insert_claim(
        conn,
        doc_obs,
        day_id,
        doc_seg,
        doc,
        kind="commitment",
        assertion="observed",
        text="Kieran will send revised pricing",
        created_at="2026-10-31T11:00:01Z",
    )
    noise_claim = _insert_claim(
        conn,
        noise_obs,
        day_id,
        noise_seg,
        noise,
        kind="activity",
        assertion="observed",
        text="Ferry timetable update",
        created_at="2026-10-31T12:00:01Z",
    )
    return {
        "day_id": day_id,
        "mail": mail_seg,
        "call": call_seg,
        "doc": doc_seg,
        "noise": noise_seg,
        "sent": sent,
        "who": who,
        "next": nxt,
        "noise_claim": noise_claim,
    }


def test_load_deal_prompts_target_v1_scope() -> None:
    cluster = load_prompt("clustering", "v2")
    summary = load_prompt("daily_summary", "v2")
    assert cluster.prompt_version == "clustering/v2"
    assert summary.prompt_version == "daily_summary/v2"
    cluster_text = cluster.text.casefold()
    assert "deal" in cluster_text
    assert "none" in cluster_text
    assert "email" in cluster_text and "call" in cluster_text
    assert "separate companies" in cluster_text
    assert "pier" in cluster_text and "cedar" in cluster_text
    summary_text = summary.text.casefold()
    assert "what_happened" in summary_text
    assert "who_involved" in summary_text
    assert "next_step" in summary_text
    assert "[c:<id>]" in summary.text


def test_clustering_schema_accepts_deals_or_parked_activities() -> None:
    deals = ClusteringOutput.model_validate(
        {
            "deals": [
                {
                    "title": "Acme Q4 proposal",
                    "summary": "same opportunity",
                    "observation_indexes": [1, 2],
                }
            ]
        }
    )
    assert len(deals.deals) == 1
    assert deals.activities == []
    parked = ClusteringOutput.model_validate(
        {
            "activities": [
                {"title": "Acme", "summary": "editing", "observation_indexes": [1]},
            ]
        }
    )
    assert parked.deals == []
    assert parked.clusters()[0].title == "Acme"
    para = SummaryParagraph.model_validate(
        {"text": "What happened: sent pricing [c:1].", "claim_ids": ["1"], "slot": "what_happened"}
    )
    assert para.slot == "what_happened"


def test_multichannel_observations_assign_one_deal_and_none_stays_none(
    tmp_path: Path,
) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(recordings=_DEAL_RECORDINGS)
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        deals = conn.execute(
            "SELECT id, title FROM deals WHERE working_day_id = ? ORDER BY title, id",
            (seeded["day_id"],),
        ).fetchall()
        assert len(deals) == 1
        assert str(deals[0]["title"]) == "Acme Q4 proposal"
        deal_id = str(deals[0]["id"])
        assigned = {
            str(row[0]): row[1]
            for row in conn.execute(
                "SELECT id, deal_id FROM segments WHERE working_day_id = ?",
                (seeded["day_id"],),
            )
        }
        assert assigned[seeded["mail"]] == deal_id
        assert assigned[seeded["call"]] == deal_id
        assert assigned[seeded["doc"]] == deal_id
        assert assigned[seeded["noise"]] is None
        counts = derived_counts(conn, seeded["day_id"])
        assert counts["deals"] == 1
        assert counts["summaries"] == 1
        row = conn.execute(
            """
            SELECT narrative_md, sections_json, prompt_version
            FROM daily_summaries WHERE working_day_id = ?
            """,
            (seeded["day_id"],),
        ).fetchone()
        assert str(row["prompt_version"]) == "daily_summary/v2"
        narrative = str(row["narrative_md"])
        assert "## Acme Q4 proposal" in narrative
        assert "What happened:" in narrative
        assert "Who involved:" in narrative
        assert "Next step:" in narrative
        assert f"[c:{seeded['sent']}]" in narrative
        assert f"[c:{seeded['noise_claim']}]" not in narrative
        assert "Ferry timetable" not in narrative
        sections = json.loads(str(row["sections_json"]))
        assert sections["deals"][0]["title"] == "Acme Q4 proposal"
        slots = {item["slot"] for item in sections["slots"]}
        assert slots == {"what_happened", "who_involved", "next_step"}
        assert all(item.get("deal_id") == deal_id for item in sections["slots"])
        assert sections["paragraphs"]
        assert all(
            isinstance(item, dict) and item.get("deal_id") == deal_id
            for item in sections["paragraphs"]
        )
        assert all(
            not any(str(cid).isdigit() for cid in item["claim_ids"]) for item in sections["slots"]
        )
    finally:
        conn.close()


@pytest.mark.parametrize(
    "title",
    (
        "none",
        "None",
        "NONE",
        "None Deal",
        " none deal ",
        "n/A",
        "No Deal",
        "no-deal",
        "na",
        "NA",
        "unassigned",
        "Unassigned",
    ),
)
def test_none_like_cluster_titles_are_unassigned(title: str) -> None:
    from watson.pipeline.consolidation import _is_none_deal_title

    assert _is_none_deal_title(title) is True


@pytest.mark.parametrize(
    "title",
    ("Nonesuch", "N/A Corp", "N/A Logistics", "Nationwide", "None of these"),
)
def test_real_deal_titles_are_not_none_aliases(title: str) -> None:
    from watson.pipeline.consolidation import _is_none_deal_title

    assert _is_none_deal_title(title) is False


def test_none_titled_cluster_is_not_stored(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        captured: list[str] = []

        class CaptureRuntime(FakeRuntime):
            def complete(self, messages, schema, *, num_predict=None):  # type: ignore[no-untyped-def]
                captured.append("\n".join(item["content"] for item in messages))
                return super().complete(messages, schema, num_predict=num_predict)

        runtime = CaptureRuntime(
            recordings={
                "complete": [
                    {
                        "match": "Cluster these observations",
                        "response": {
                            "deals": [
                                {
                                    "title": "none",
                                    "summary": "not a deal",
                                    "observation_indexes": [1, 2, 3, 4],
                                }
                            ]
                        },
                    },
                    {
                        "match": "Extract cross-segment",
                        "response": {
                            "entity_deltas": [],
                            "decisions": [],
                            "commitments": [],
                        },
                    },
                    {
                        "match": "Write the daily narrative",
                        "response": {
                            "paragraphs": [
                                {
                                    "text": "Noise leaked [c:1].",
                                    "claim_ids": ["1"],
                                    "slot": "what_happened",
                                }
                            ]
                        },
                    },
                ]
            }
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        assert derived_counts(conn, seeded["day_id"])["deals"] == 0
        nulls = conn.execute(
            "SELECT COUNT(*) FROM segments WHERE working_day_id = ? AND deal_id IS NOT NULL",
            (seeded["day_id"],),
        ).fetchone()[0]
        assert int(nulls) == 0
        row = conn.execute(
            "SELECT narrative_md, sections_json FROM daily_summaries WHERE working_day_id = ?",
            (seeded["day_id"],),
        ).fetchone()
        narrative = str(row["narrative_md"])
        assert narrative.strip()
        assert "## none" not in narrative
        assert "Noise leaked" not in narrative
        assert f"[c:{seeded['noise_claim']}]" in narrative
        assert "Ferry timetable" in narrative
        assert not any("Write the daily narrative" in blob for blob in captured)
        sections = json.loads(str(row["sections_json"]))
        assert sections["deals"] == []
        assert sections["slots"] == []
    finally:
        conn.close()


def test_none_deal_day_writes_cited_fallback_narrative(tmp_path: Path) -> None:
    from watson.intelligence.eval.score import collect_predicted

    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(
            recordings={
                "complete": [
                    {
                        "match": "Cluster these observations",
                        "response": {
                            "deals": [
                                {
                                    "title": "None Deal",
                                    "summary": "not a deal",
                                    "observation_indexes": [1, 2, 3, 4],
                                }
                            ]
                        },
                    },
                    {
                        "match": "Extract cross-segment",
                        "response": {
                            "entity_deltas": [],
                            "decisions": [],
                            "commitments": [],
                        },
                    },
                    {
                        "match": "Write the daily narrative",
                        "response": {
                            "paragraphs": [
                                {
                                    "text": "Invented wrap-up [c:1].",
                                    "claim_ids": ["1"],
                                    "slot": "what_happened",
                                }
                            ]
                        },
                    },
                ]
            }
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        assert derived_counts(conn, seeded["day_id"])["deals"] == 0
        row = conn.execute(
            "SELECT narrative_md FROM daily_summaries WHERE working_day_id = ?",
            (seeded["day_id"],),
        ).fetchone()
        narrative = str(row["narrative_md"])
        assert narrative.strip()
        assert "Invented wrap-up" not in narrative
        assert f"[c:{seeded['sent']}]" in narrative
        assert f"[c:{seeded['who']}]" in narrative
        assert f"[c:{seeded['next']}]" in narrative
        assert f"[c:{seeded['noise_claim']}]" in narrative
        predicted = collect_predicted(conn, seeded["day_id"])
        assert predicted["citations_resolvable"] == 1.0
        assert predicted["narrative_nonempty"] is True
        assert predicted["deal_titles"] == []
    finally:
        conn.close()


def test_duplicate_observation_index_first_deal_wins(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(
            recordings={
                "complete": [
                    {
                        "match": "Cluster these observations",
                        "response": {
                            "deals": [
                                {
                                    "title": "Northwind Renewal",
                                    "summary": "first",
                                    "observation_indexes": [1, 2],
                                },
                                {
                                    "title": "Expansion Seats",
                                    "summary": "duplicate index 2",
                                    "observation_indexes": [2, 3],
                                },
                            ]
                        },
                    },
                    {
                        "match": "Extract cross-segment",
                        "response": {
                            "entity_deltas": [],
                            "decisions": [],
                            "commitments": [],
                        },
                    },
                    {
                        "match": "Write the daily narrative",
                        "response": {
                            "paragraphs": [
                                {
                                    "text": "What happened: renewal work [c:1].",
                                    "claim_ids": ["1"],
                                    "slot": "what_happened",
                                }
                            ]
                        },
                    },
                ]
            }
        )
        with caplog.at_level("WARNING", logger="watson.pipeline.consolidation"):
            consolidate_working_day(
                conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW
            )
        titles = [
            str(row[0])
            for row in conn.execute(
                "SELECT title FROM deals WHERE working_day_id = ? ORDER BY created_at, id",
                (seeded["day_id"],),
            )
        ]
        assert "Northwind Renewal" in titles
        assert "Expansion Seats" in titles
        by_segment = {
            str(row[0]): str(row[1] or "")
            for row in conn.execute(
                """
                SELECT s.id, d.title
                FROM segments s
                LEFT JOIN deals d ON d.id = s.deal_id
                WHERE s.working_day_id = ?
                """,
                (seeded["day_id"],),
            )
        }
        assert by_segment[seeded["mail"]] == "Northwind Renewal"
        assert by_segment[seeded["call"]] == "Northwind Renewal"
        assert by_segment[seeded["doc"]] == "Expansion Seats"
        assert "duplicate observation index dropped" in caplog.text
    finally:
        conn.close()


def test_duplicate_observation_call_claim_cited_only_under_first_deal(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(
            recordings={
                "complete": [
                    {
                        "match": "Cluster these observations",
                        "response": {
                            "deals": [
                                {
                                    "title": "Northwind Renewal",
                                    "summary": "first",
                                    "observation_indexes": [1, 2],
                                },
                                {
                                    "title": "Expansion Seats",
                                    "summary": "duplicate index 2",
                                    "observation_indexes": [2, 3],
                                },
                            ]
                        },
                    },
                    {
                        "match": "Extract cross-segment",
                        "response": {
                            "entity_deltas": [],
                            "decisions": [],
                            "commitments": [],
                        },
                    },
                    {
                        "match": "Write the daily narrative",
                        "response": {
                            "paragraphs": [
                                {
                                    "text": "Invented wrap-up",
                                    "claim_ids": [],
                                    "slot": "what_happened",
                                }
                            ]
                        },
                    },
                ]
            }
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        narrative = str(
            conn.execute(
                "SELECT narrative_md FROM daily_summaries WHERE working_day_id = ?",
                (seeded["day_id"],),
            ).fetchone()[0]
        )
        call_cite = f"[c:{seeded['who']}]"
        by_title: dict[str, str] = {}
        for part in narrative.split("## "):
            title, sep, body = part.partition("\n")
            if not sep:
                continue
            by_title[title.strip()] = body
        assert "Northwind Renewal" in by_title
        assert call_cite in by_title["Northwind Renewal"]
        for title, body in by_title.items():
            if title != "Northwind Renewal":
                assert call_cite not in body, f"{call_cite} leaked into {title!r}"
    finally:
        conn.close()


def test_duplicate_observation_drop_logs_ids_not_titles(tmp_path: Path) -> None:
    confidential = "Project Bluebird acquisition of Contoso (confidential)"
    conn = _open(tmp_path)
    watson_log = logging.getLogger("watson")
    old_handlers = list(watson_log.handlers)
    old_level = watson_log.level
    old_propagate = watson_log.propagate
    buf = io.StringIO()
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(
            recordings={
                "complete": [
                    {
                        "match": "Cluster these observations",
                        "response": {
                            "deals": [
                                {
                                    "title": confidential,
                                    "summary": "first",
                                    "observation_indexes": [1, 2],
                                },
                                {
                                    "title": f"{confidential} follow-on",
                                    "summary": "duplicate index 2",
                                    "observation_indexes": [2, 3],
                                },
                            ]
                        },
                    },
                    {
                        "match": "Extract cross-segment",
                        "response": {
                            "entity_deltas": [],
                            "decisions": [],
                            "commitments": [],
                        },
                    },
                    {
                        "match": "Write the daily narrative",
                        "response": {
                            "paragraphs": [
                                {
                                    "text": "What happened: renewal work [c:1].",
                                    "claim_ids": ["1"],
                                    "slot": "what_happened",
                                }
                            ]
                        },
                    },
                ]
            }
        )
        configure_logging(level=logging.DEBUG, stream=buf)
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        output = buf.getvalue()
        assert confidential not in output
        assert "Bluebird" not in output
        assert "Contoso" not in output
        assert "follow-on" not in output
        logged = False
        for line in output.splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            extra = payload.get("extra") or {}
            assert confidential not in json.dumps(payload)
            if payload.get("msg") == "duplicate observation index dropped":
                logged = True
                assert "deal" not in extra
                assert extra["dropped_count"] >= 1
                assert extra["kept_count"] >= 0
                assert extra["dropped_ids"]
                assert extra["dropped_indexes"]
                assert all(isinstance(item, str) for item in extra["dropped_ids"])
                assert all(isinstance(item, int) for item in extra["dropped_indexes"])
        assert logged
    finally:
        watson_log.handlers.clear()
        for handler in old_handlers:
            watson_log.addHandler(handler)
        watson_log.setLevel(old_level)
        watson_log.propagate = old_propagate
        conn.close()


def test_uncited_invented_summary_paragraph_is_dropped(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(
            recordings={
                "complete": [
                    {
                        "match": "Cluster these observations",
                        "response": {
                            "deals": [
                                {
                                    "title": "Acme Q4 proposal",
                                    "summary": "same opportunity",
                                    "observation_indexes": [1, 2, 3],
                                }
                            ]
                        },
                    },
                    {
                        "match": "Extract cross-segment",
                        "response": {
                            "entity_deltas": [],
                            "decisions": [],
                            "commitments": [],
                        },
                    },
                    {
                        "match": "Write the daily narrative",
                        "response": {
                            "paragraphs": [
                                {
                                    "text": "John and Jane were involved",
                                    "claim_ids": [],
                                    "slot": "who_involved",
                                },
                                {
                                    "text": "The deal was completed",
                                    "claim_ids": [],
                                    "slot": "what_happened",
                                },
                                {
                                    "text": "What happened: Sent the pricing pack [c:1].",
                                    "claim_ids": ["1"],
                                    "slot": "what_happened",
                                },
                            ]
                        },
                    },
                ]
            }
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        row = conn.execute(
            "SELECT narrative_md FROM daily_summaries WHERE working_day_id = ?",
            (seeded["day_id"],),
        ).fetchone()
        narrative = str(row["narrative_md"])
        assert "John and Jane" not in narrative
        assert "The deal was completed" not in narrative
        assert f"[c:{seeded['sent']}]" in narrative
    finally:
        conn.close()


def test_claim_ids_without_inline_citations_still_write_slots(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(
            recordings=_recordings_with_summary(
                [
                    {
                        "text": "What happened: Sent the pricing pack.",
                        "claim_ids": ["1"],
                        "slot": "what_happened",
                    }
                ]
            )
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        row = conn.execute(
            "SELECT narrative_md, sections_json FROM daily_summaries WHERE working_day_id = ?",
            (seeded["day_id"],),
        ).fetchone()
        narrative = str(row["narrative_md"])
        sections = json.loads(str(row["sections_json"]))
        assert "Sent the pricing pack" in narrative
        assert f"[c:{seeded['sent']}]" in narrative
        slots = {item["slot"]: item for item in sections["slots"]}
        assert slots["what_happened"]["claim_ids"] == [seeded["sent"]]
        assert all(
            not any(str(cid).isdigit() for cid in item["claim_ids"]) for item in sections["slots"]
        )
    finally:
        conn.close()


def test_slot_inferred_from_prefix_when_model_omits_slot(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(
            recordings=_recordings_with_summary(
                [
                    {
                        "text": "Who involved: Priya joined the call [c:2].",
                        "claim_ids": ["2"],
                    }
                ]
            )
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        row = conn.execute(
            "SELECT narrative_md, sections_json FROM daily_summaries WHERE working_day_id = ?",
            (seeded["day_id"],),
        ).fetchone()
        sections = json.loads(str(row["sections_json"]))
        slots = {item["slot"]: item for item in sections["slots"]}
        assert slots["who_involved"]["claim_ids"] == [seeded["who"]]
        assert all(
            not any(str(cid).isdigit() for cid in item["claim_ids"]) for item in sections["slots"]
        )
    finally:
        conn.close()


def test_empty_model_paragraphs_fallback_writes_slots(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(recordings=_recordings_with_summary([]))
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        row = conn.execute(
            "SELECT narrative_md, sections_json FROM daily_summaries WHERE working_day_id = ?",
            (seeded["day_id"],),
        ).fetchone()
        narrative = str(row["narrative_md"])
        sections = json.loads(str(row["sections_json"]))
        slots = {item["slot"]: item for item in sections["slots"]}
        assert set(slots) == {"what_happened", "who_involved", "next_step"}
        assert slots["what_happened"]["claim_ids"] == [seeded["sent"]]
        assert slots["who_involved"]["claim_ids"] == [seeded["who"]]
        assert slots["next_step"]["claim_ids"] == [seeded["next"]]
        assert all(item.get("deal_id") not in {"", "none"} for item in sections["slots"])
        assert all(
            not any(str(cid).isdigit() for cid in item["claim_ids"]) for item in sections["slots"]
        )
        cited = {cid for item in sections["slots"] for cid in item["claim_ids"]}
        assert seeded["noise_claim"] not in cited
        assert f"[c:{seeded['sent']}]" in narrative
        assert f"[c:{seeded['who']}]" in narrative
        assert f"[c:{seeded['next']}]" in narrative
    finally:
        conn.close()


def test_activities_recording_promotes_to_a_deal(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        runtime = _runtime()
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        counts = derived_counts(conn, seeded["day_id"])
        assert counts["activities"] == 1
        assert counts["deals"] == 1
        title = conn.execute(
            "SELECT title FROM deals WHERE working_day_id = ?", (seeded["day_id"],)
        ).fetchone()[0]
        assert str(title) == "Acme Q4 proposal"
        assigned = conn.execute(
            "SELECT deal_id FROM segments WHERE id = ?", (seeded["changed"],)
        ).fetchone()[0]
        assert assigned is not None
        row = conn.execute(
            "SELECT prompt_version FROM deals WHERE working_day_id = ?",
            (seeded["day_id"],),
        ).fetchone()
        assert str(row["prompt_version"]) == "clustering/v2"
        first = counts
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        assert derived_counts(conn, seeded["day_id"]) == first
    finally:
        conn.close()


def test_fallback_clustering_does_not_invent_deals(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_day(conn)
        runtime = FakeRuntime(
            complete_queue=[
                OutputParseError("truncated clustering JSON"),
                OutputParseError("truncated clustering JSON"),
                {"entity_deltas": [], "decisions": [], "commitments": []},
                {"paragraphs": [{"text": "Work continued [c:1].", "claim_ids": ["1"]}]},
            ]
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        counts = derived_counts(conn, seeded["day_id"])
        assert counts["activities"] == 2
        assert counts["deals"] == 0
        assigned = int(
            conn.execute(
                "SELECT COUNT(*) FROM segments WHERE working_day_id = ? AND deal_id IS NOT NULL",
                (seeded["day_id"],),
            ).fetchone()[0]
        )
        assert assigned == 0
    finally:
        conn.close()


def test_clustering_prompt_includes_understanding_deal_candidates(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        captured: list[str] = []

        class CaptureRuntime(FakeRuntime):
            def complete(self, messages, schema, *, num_predict=None):  # type: ignore[no-untyped-def]
                captured.append("\n".join(item["content"] for item in messages))
                return super().complete(messages, schema, num_predict=num_predict)

        runtime = CaptureRuntime(recordings=_DEAL_RECORDINGS)
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        cluster_prompts = [blob for blob in captured if "Cluster these observations" in blob]
        assert cluster_prompts
        prompt = cluster_prompts[0]
        assert "deal candidates: Acme Q4 proposal" in prompt
        assert "none is its own class" in prompt
        assert "Separate companies are separate deals" in prompt
        assert "Pier invoice" in prompt
        assert "cedar onboarding" in prompt.casefold()
        narrative_prompts = [blob for blob in captured if "Write the daily narrative" in blob]
        assert narrative_prompts
        assert "Deal: Acme Q4 proposal" in narrative_prompts[0]
        assert "what happened" in narrative_prompts[0].casefold()
        assert "Roster names:" in narrative_prompts[0]
        assert "[c:" in narrative_prompts[0]
    finally:
        conn.close()


def test_summary_roster_lists_day_people_not_projects(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        _insert_entity(conn, name="Priya Natarajan", typ="person")
        _insert_entity(conn, name="Jordan Hale", typ="person")
        _insert_entity(conn, name="Secret Skunkworks", typ="project")
        conn.execute(
            """
            UPDATE observations
            SET raw_json = ?
            WHERE segment_id IN (?, ?, ?)
            """,
            (
                json.dumps(
                    {
                        "project_candidates": ["Acme Q4 proposal"],
                        "people_candidates": ["Priya Natarajan"],
                    }
                ),
                seeded["mail"],
                seeded["call"],
                seeded["doc"],
            ),
        )
        conn.commit()
        captured: list[str] = []

        class CaptureRuntime(FakeRuntime):
            def complete(self, messages, schema, *, num_predict=None):  # type: ignore[no-untyped-def]
                captured.append("\n".join(item["content"] for item in messages))
                return super().complete(messages, schema, num_predict=num_predict)

        runtime = CaptureRuntime(recordings=_DEAL_RECORDINGS)
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        narrative_prompts = [blob for blob in captured if "Write the daily narrative" in blob]
        assert narrative_prompts
        prompt = narrative_prompts[0]
        assert "Roster names:" in prompt
        assert "Priya Natarajan" in prompt
        assert "Jordan Hale" not in prompt
        assert "Secret Skunkworks" not in prompt
    finally:
        conn.close()


def test_day_roster_scopes_to_day_people_and_caps() -> None:
    from watson.pipeline.consolidation import _ROSTER_CAP, _day_roster

    entities = [
        {"type": "person", "canonical_name": "Priya Natarajan"},
        {"type": "person", "canonical_name": "Jordan Hale"},
        {"type": "project", "canonical_name": "Secret Skunkworks"},
    ]
    observations = [
        {"raw_json": json.dumps({"people_candidates": ["Priya Natarajan"]})},
    ]
    assert _day_roster(entities, observations) == ["Priya Natarajan"]

    many = [{"type": "person", "canonical_name": f"Person {index:02d}"} for index in range(20)]
    day_names = [row["canonical_name"] for row in many]
    capped = _day_roster(
        many,
        [{"raw_json": json.dumps({"people_candidates": day_names})}],
    )
    assert capped == day_names[:_ROSTER_CAP]
    assert len(capped) == 12


def test_eval_collect_predicted_exposes_segment_deals(tmp_path: Path) -> None:
    from watson.intelligence.eval.score import collect_predicted

    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(recordings=_DEAL_RECORDINGS)
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        predicted = collect_predicted(conn, seeded["day_id"])
        assert predicted["deal_titles"] == ["Acme Q4 proposal"]
        by_segment = {row["segment_id"]: row for row in predicted["segment_deals"]}
        assert by_segment[seeded["mail"]]["title"] == "Acme Q4 proposal"
        assert by_segment[seeded["call"]]["deal_id"] != "none"
        assert by_segment[seeded["doc"]]["title"] == "Acme Q4 proposal"
        assert by_segment[seeded["noise"]]["deal_id"] == "none"
        assert by_segment[seeded["noise"]]["title"] == "none"
        slots = {item["slot"] for item in predicted["summary_slots"]}
        assert slots == {"what_happened", "who_involved", "next_step"}
        assert all(item.get("deal_id") not in {"", "none"} for item in predicted["summary_slots"])
        assert all(
            not any(str(cid).isdigit() for cid in item["claim_ids"])
            for item in predicted["summary_slots"]
        )
        assert seeded["noise_claim"] not in {
            cid for item in predicted["summary_slots"] for cid in item["claim_ids"]
        }
        deal_id = by_segment[seeded["mail"]]["deal_id"]
        conn.execute(
            """
            UPDATE daily_summaries
            SET sections_json = ?
            WHERE working_day_id = ?
            """,
            (
                json.dumps(
                    {
                        "deals": [{"id": deal_id, "title": "Acme Q4 proposal"}],
                        "slots": [
                            {
                                "slot": "what_happened",
                                "claim_ids": ["1"],
                                "deal_id": deal_id,
                            },
                            {
                                "slot": "who_involved",
                                "claim_ids": [seeded["who"]],
                                "deal_id": "",
                            },
                            {
                                "slot": "next_step",
                                "claim_ids": [seeded["next"]],
                                "deal_id": deal_id,
                            },
                        ],
                    }
                ),
                seeded["day_id"],
            ),
        )
        conn.commit()
        filtered = collect_predicted(conn, seeded["day_id"])["summary_slots"]
        assert all(not any(str(cid).isdigit() for cid in item["claim_ids"]) for item in filtered)
        assert all(item.get("deal_id") for item in filtered)
        assert {item["slot"] for item in filtered} == {"next_step"}
    finally:
        conn.close()


def test_summary_prompt_and_body_omit_noise_claims(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        captured: list[str] = []

        class CaptureRuntime(FakeRuntime):
            def complete(self, messages, schema, *, num_predict=None):  # type: ignore[no-untyped-def]
                captured.append("\n".join(item["content"] for item in messages))
                return super().complete(messages, schema, num_predict=num_predict)

        runtime = CaptureRuntime(recordings=_DEAL_RECORDINGS)
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        narrative_prompts = [blob for blob in captured if "Write the daily narrative" in blob]
        assert narrative_prompts
        prompt = narrative_prompts[0]
        assert seeded["sent"] in prompt
        assert seeded["noise_claim"] not in prompt
        assert "Ferry timetable" not in prompt
        narrative = str(
            conn.execute(
                "SELECT narrative_md FROM daily_summaries WHERE working_day_id = ?",
                (seeded["day_id"],),
            ).fetchone()[0]
        )
        assert "Ferry timetable" not in narrative
        assert f"[c:{seeded['noise_claim']}]" not in narrative
    finally:
        conn.close()


def test_invalid_citation_does_not_dump_noise_into_deal_summary(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(
            recordings={
                "complete": [
                    {
                        "match": "Cluster these observations",
                        "response": {
                            "deals": [
                                {
                                    "title": "Acme Q4 proposal",
                                    "summary": "same opportunity",
                                    "observation_indexes": [1, 2, 3],
                                }
                            ]
                        },
                    },
                    {
                        "match": "Extract cross-segment",
                        "response": {
                            "entity_deltas": [],
                            "decisions": [],
                            "commitments": [],
                        },
                    },
                    {
                        "match": "Write the daily narrative",
                        "response": {
                            "paragraphs": [
                                {
                                    "text": "Ghost cite [c:99].",
                                    "claim_ids": ["99"],
                                    "slot": "what_happened",
                                }
                            ]
                        },
                    },
                ]
            }
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        row = conn.execute(
            "SELECT narrative_md, sections_json FROM daily_summaries WHERE working_day_id = ?",
            (seeded["day_id"],),
        ).fetchone()
        narrative = str(row["narrative_md"])
        assert "Ghost cite" not in narrative
        assert "[c:99]" not in narrative
        assert "Ferry timetable" not in narrative
        assert f"[c:{seeded['noise_claim']}]" not in narrative
        assert "## Acme Q4 proposal" in narrative
        assert f"[c:{seeded['sent']}]" in narrative
        sections = json.loads(str(row["sections_json"]))
        assert sections["slots"]
        assert all(
            not any(str(cid).isdigit() for cid in item["claim_ids"]) for item in sections["slots"]
        )
        assert all(seeded["noise_claim"] not in item["claim_ids"] for item in sections["slots"])
        assert all(item.get("id") and item.get("title") for item in sections["deals"])
    finally:
        conn.close()


def test_sentence_citing_deal_and_noise_is_dropped(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(
            recordings={
                "complete": [
                    {
                        "match": "Cluster these observations",
                        "response": {
                            "deals": [
                                {
                                    "title": "Acme Q4 proposal",
                                    "summary": "same opportunity",
                                    "observation_indexes": [1, 2, 3],
                                }
                            ]
                        },
                    },
                    {
                        "match": "Extract cross-segment",
                        "response": {
                            "entity_deltas": [],
                            "decisions": [],
                            "commitments": [],
                        },
                    },
                    {
                        "match": "Write the daily narrative",
                        "response": {
                            "paragraphs": [
                                {
                                    "text": (
                                        f"What happened: Sent the pricing pack "
                                        f"[c:{seeded['sent']}]."
                                    ),
                                    "claim_ids": [seeded["sent"]],
                                    "slot": "what_happened",
                                },
                                {
                                    "text": (
                                        f"Also the ferry timetable "
                                        f"[c:{seeded['sent']}] "
                                        f"[c:{seeded['noise_claim']}]."
                                    ),
                                    "claim_ids": [seeded["sent"], seeded["noise_claim"]],
                                    "slot": "who_involved",
                                },
                            ]
                        },
                    },
                ]
            }
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        row = conn.execute(
            "SELECT narrative_md, sections_json FROM daily_summaries WHERE working_day_id = ?",
            (seeded["day_id"],),
        ).fetchone()
        narrative = str(row["narrative_md"])
        assert "ferry" not in narrative.casefold()
        assert f"[c:{seeded['noise_claim']}]" not in narrative
        assert f"[c:{seeded['sent']}]" in narrative
        sections = json.loads(str(row["sections_json"]))
        assert all(seeded["noise_claim"] not in item["claim_ids"] for item in sections["slots"])
    finally:
        conn.close()


def test_summary_deal_sections_keep_validated_slots_only() -> None:
    payload = _summary_deal_sections(
        [{"id": "deal-1", "title": "Acme Q4 proposal", "index": 0}],
        [
            (0, "what_happened", ["1"]),
            (0, "who_involved", [""]),
            (1, "next_step", ["claim-uuid"]),
            (0, "next_step", ["claim-uuid"]),
        ],
    )
    assert payload["slots"] == [
        {"slot": "next_step", "claim_ids": ["claim-uuid"], "deal_id": "deal-1"}
    ]
    assert "paragraphs" not in payload


def test_summary_deal_sections_store_paragraph_deal_ids() -> None:
    payload = _summary_deal_sections(
        [
            {"id": "deal-1", "title": "Acme Q4", "index": 0},
            {"id": "deal-2", "title": "Globex intro", "index": 1},
            {"id": "deal-3", "title": "Initech renewal", "index": 2},
        ],
        [(0, "what_happened", ["claim-a"])],
        {
            0: ["What happened: Sent the pricing pack [c:claim-a]."],
            2: ["Drafted the fallback cite [c:claim-c]."],
        },
    )
    by_deal = {item["deal_id"]: item["text"] for item in payload["paragraphs"]}
    assert set(by_deal) == {"deal-1", "deal-3"}
    assert by_deal["deal-1"].startswith("## Acme Q4")
    assert "Sent the pricing pack" in by_deal["deal-1"]
    assert "fallback cite" in by_deal["deal-3"]
    assert "Globex" not in " ".join(by_deal.values())


def test_summary_deal_sections_duplicate_titles_keep_separate_deal_ids() -> None:
    payload = _summary_deal_sections(
        [
            {"id": "deal-1", "title": "Acme", "index": 0},
            {"id": "deal-2", "title": "Acme", "index": 1},
        ],
        [
            (0, "what_happened", ["claim-a"]),
            (1, "what_happened", ["claim-b"]),
        ],
    )
    by_deal = {item["deal_id"]: item for item in payload["slots"]}
    assert by_deal["deal-1"]["claim_ids"] == ["claim-a"]
    assert by_deal["deal-2"]["claim_ids"] == ["claim-b"]


def test_two_deal_and_cross_deal_citations(tmp_path: Path) -> None:
    from tests.unit.test_consolidation import _insert_day

    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        acme_frame = _insert_frame(conn, day_id, captured_at="2026-10-31T09:00:00Z")
        globex_frame = _insert_frame(conn, day_id, captured_at="2026-10-31T10:00:00Z")
        acme_seg = _insert_segment(
            conn, day_id, acme_frame, started_at="2026-10-31T09:00:00Z", merged="Acme mail"
        )
        globex_seg = _insert_segment(
            conn, day_id, globex_frame, started_at="2026-10-31T10:00:00Z", merged="Globex mail"
        )
        acme_obs = _insert_observation(
            conn, acme_seg, description="Emailing Acme", project_candidates=["Acme Q4 proposal"]
        )
        globex_obs = _insert_observation(
            conn, globex_seg, description="Emailing Globex", project_candidates=["Globex pilot"]
        )
        acme_claim = _insert_claim(
            conn,
            acme_obs,
            day_id,
            acme_seg,
            acme_frame,
            kind="activity",
            assertion="observed",
            text="Sent Acme pricing",
            created_at="2026-10-31T09:00:01Z",
        )
        globex_claim = _insert_claim(
            conn,
            globex_obs,
            day_id,
            globex_seg,
            globex_frame,
            kind="activity",
            assertion="observed",
            text="Sent Globex intro",
            created_at="2026-10-31T10:00:01Z",
        )
        leftover = "Unmatched leftover paragraph with no deal."
        runtime = FakeRuntime(
            recordings={
                "complete": [
                    {
                        "match": "Cluster these observations",
                        "response": {
                            "deals": [
                                {
                                    "title": "Acme Q4 proposal",
                                    "summary": "Acme thread",
                                    "observation_indexes": [1],
                                },
                                {
                                    "title": "Globex pilot",
                                    "summary": "Globex thread",
                                    "observation_indexes": [2],
                                },
                            ]
                        },
                    },
                    {
                        "match": "Extract cross-segment",
                        "response": {
                            "entity_deltas": [],
                            "decisions": [],
                            "commitments": [],
                        },
                    },
                    {
                        "match": "Write the daily narrative",
                        "response": {
                            "paragraphs": [
                                {
                                    "text": f"What happened: Sent Acme pricing [c:{acme_claim}].",
                                    "claim_ids": [acme_claim],
                                    "slot": "what_happened",
                                },
                                {
                                    "text": f"What happened: Sent Globex intro [c:{globex_claim}].",
                                    "claim_ids": [globex_claim],
                                    "slot": "what_happened",
                                },
                                {
                                    "text": (
                                        f"Mixed both [c:{acme_claim}] and [c:{globex_claim}]."
                                    ),
                                    "claim_ids": [acme_claim, globex_claim],
                                    "slot": "what_happened",
                                },
                                {
                                    "text": leftover,
                                    "claim_ids": [],
                                    "slot": "next_step",
                                },
                            ]
                        },
                    },
                ]
            }
        )
        consolidate_working_day(conn, day_id, runtime=runtime, embedding=runtime, now=NOW)
        row = conn.execute(
            "SELECT narrative_md, sections_json FROM daily_summaries WHERE working_day_id = ?",
            (day_id,),
        ).fetchone()
        narrative = str(row["narrative_md"])
        assert narrative.index("## Acme Q4 proposal") < narrative.index("## Globex pilot")
        acme_block = narrative.split("## Globex pilot")[0]
        globex_block = narrative.split("## Globex pilot")[1]
        assert f"[c:{acme_claim}]" in acme_block
        assert f"[c:{globex_claim}]" not in acme_block
        assert f"[c:{globex_claim}]" in globex_block
        assert f"[c:{acme_claim}]" not in globex_block
        assert "Mixed both" not in narrative
        assert leftover not in narrative
        sections = json.loads(str(row["sections_json"]))
        by_deal = {item["title"]: item["id"] for item in sections["deals"]}
        slots = sections["slots"]
        assert {item["slot"] for item in slots} == {"what_happened"}
        assert {item["deal_id"] for item in slots} == set(by_deal.values())
        acme_slot = next(item for item in slots if item["deal_id"] == by_deal["Acme Q4 proposal"])
        globex_slot = next(item for item in slots if item["deal_id"] == by_deal["Globex pilot"])
        assert acme_slot["claim_ids"] == [acme_claim]
        assert globex_slot["claim_ids"] == [globex_claim]
    finally:
        conn.close()


def test_duplicate_titles_keep_separate_headings_and_slots(tmp_path: Path) -> None:
    from tests.unit.test_consolidation import _insert_day

    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        first_frame = _insert_frame(conn, day_id, captured_at="2026-10-31T09:00:00Z")
        second_frame = _insert_frame(conn, day_id, captured_at="2026-10-31T10:00:00Z")
        first_seg = _insert_segment(
            conn, day_id, first_frame, started_at="2026-10-31T09:00:00Z", merged="Acme mail"
        )
        second_seg = _insert_segment(
            conn, day_id, second_frame, started_at="2026-10-31T10:00:00Z", merged="Acme renewal"
        )
        first_obs = _insert_observation(conn, first_seg, description="Emailing Acme pricing")
        second_obs = _insert_observation(conn, second_seg, description="Emailing Acme renewal")
        first_claim = _insert_claim(
            conn,
            first_obs,
            day_id,
            first_seg,
            first_frame,
            kind="activity",
            assertion="observed",
            text="Sent Acme pricing",
            created_at="2026-10-31T09:00:01Z",
        )
        second_claim = _insert_claim(
            conn,
            second_obs,
            day_id,
            second_seg,
            second_frame,
            kind="activity",
            assertion="observed",
            text="Sent Acme renewal",
            created_at="2026-10-31T10:00:01Z",
        )
        runtime = FakeRuntime(
            recordings={
                "complete": [
                    {
                        "match": "Cluster these observations",
                        "response": {
                            "deals": [
                                {
                                    "title": "Acme",
                                    "summary": "pricing thread",
                                    "observation_indexes": [1],
                                },
                                {
                                    "title": "Acme",
                                    "summary": "renewal thread",
                                    "observation_indexes": [2],
                                },
                            ]
                        },
                    },
                    {
                        "match": "Extract cross-segment",
                        "response": {
                            "entity_deltas": [],
                            "decisions": [],
                            "commitments": [],
                        },
                    },
                    {
                        "match": "Write the daily narrative",
                        "response": {
                            "paragraphs": [
                                {
                                    "text": (
                                        f"What happened: Sent Acme pricing [c:{first_claim}]."
                                    ),
                                    "claim_ids": [first_claim],
                                    "slot": "what_happened",
                                },
                                {
                                    "text": (
                                        f"What happened: Sent Acme renewal [c:{second_claim}]."
                                    ),
                                    "claim_ids": [second_claim],
                                    "slot": "what_happened",
                                },
                            ]
                        },
                    },
                ]
            }
        )
        consolidate_working_day(conn, day_id, runtime=runtime, embedding=runtime, now=NOW)
        row = conn.execute(
            "SELECT narrative_md, sections_json FROM daily_summaries WHERE working_day_id = ?",
            (day_id,),
        ).fetchone()
        narrative = str(row["narrative_md"])
        blocks = [chunk for chunk in narrative.split("## Acme") if chunk.strip()]
        assert len(blocks) == 2
        assert f"[c:{first_claim}]" in blocks[0]
        assert f"[c:{second_claim}]" not in blocks[0]
        assert f"[c:{second_claim}]" in blocks[1]
        assert f"[c:{first_claim}]" not in blocks[1]
        sections = json.loads(str(row["sections_json"]))
        deals = sections["deals"]
        assert [item["title"] for item in deals] == ["Acme", "Acme"]
        assert deals[0]["id"] != deals[1]["id"]
        by_deal = {item["deal_id"]: item for item in sections["slots"]}
        assert by_deal[deals[0]["id"]]["claim_ids"] == [first_claim]
        assert by_deal[deals[1]["id"]]["claim_ids"] == [second_claim]
    finally:
        conn.close()


def test_leading_none_cluster_keeps_later_deal_slots(tmp_path: Path) -> None:
    from tests.unit.test_consolidation import _insert_day

    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        noise_frame = _insert_frame(conn, day_id, captured_at="2026-10-31T08:00:00Z")
        acme_frame = _insert_frame(conn, day_id, captured_at="2026-10-31T09:00:00Z")
        globex_frame = _insert_frame(conn, day_id, captured_at="2026-10-31T10:00:00Z")
        noise_seg = _insert_segment(
            conn, day_id, noise_frame, started_at="2026-10-31T08:00:00Z", merged="Harbor news"
        )
        acme_seg = _insert_segment(
            conn, day_id, acme_frame, started_at="2026-10-31T09:00:00Z", merged="Acme mail"
        )
        globex_seg = _insert_segment(
            conn, day_id, globex_frame, started_at="2026-10-31T10:00:00Z", merged="Globex mail"
        )
        _insert_observation(conn, noise_seg, description="Reading civic news")
        acme_obs = _insert_observation(conn, acme_seg, description="Emailing Acme")
        globex_obs = _insert_observation(conn, globex_seg, description="Emailing Globex")
        acme_claim = _insert_claim(
            conn,
            acme_obs,
            day_id,
            acme_seg,
            acme_frame,
            kind="commitment",
            assertion="observed",
            text="Send Acme pricing Friday",
            created_at="2026-10-31T09:00:01Z",
        )
        globex_claim = _insert_claim(
            conn,
            globex_obs,
            day_id,
            globex_seg,
            globex_frame,
            kind="commitment",
            assertion="observed",
            text="Send Globex intro Monday",
            created_at="2026-10-31T10:00:01Z",
        )
        runtime = FakeRuntime(
            recordings={
                "complete": [
                    {
                        "match": "Cluster these observations",
                        "response": {
                            "deals": [
                                {
                                    "title": "none",
                                    "summary": "noise",
                                    "observation_indexes": [1],
                                },
                                {
                                    "title": "Acme Q4 proposal",
                                    "summary": "Acme thread",
                                    "observation_indexes": [2],
                                },
                                {
                                    "title": "Globex pilot",
                                    "summary": "Globex thread",
                                    "observation_indexes": [3],
                                },
                            ]
                        },
                    },
                    {
                        "match": "Extract cross-segment",
                        "response": {
                            "entity_deltas": [],
                            "decisions": [],
                            "commitments": [],
                        },
                    },
                    {
                        "match": "Write the daily narrative",
                        "response": {
                            "paragraphs": [
                                {
                                    "text": (
                                        f"Next step: Send Acme pricing Friday [c:{acme_claim}]."
                                    ),
                                    "claim_ids": [acme_claim],
                                    "slot": "next_step",
                                },
                                {
                                    "text": (
                                        f"Next step: Send Globex intro Monday [c:{globex_claim}]."
                                    ),
                                    "claim_ids": [globex_claim],
                                    "slot": "next_step",
                                },
                            ]
                        },
                    },
                ]
            }
        )
        consolidate_working_day(conn, day_id, runtime=runtime, embedding=runtime, now=NOW)
        stored = {
            str(row["title"]): str(row["id"])
            for row in conn.execute(
                "SELECT id, title FROM deals WHERE working_day_id = ?",
                (day_id,),
            )
        }
        assert set(stored) == {"Acme Q4 proposal", "Globex pilot"}
        acme_id = stored["Acme Q4 proposal"]
        globex_id = stored["Globex pilot"]
        assert (
            conn.execute("SELECT deal_id FROM segments WHERE id = ?", (noise_seg,)).fetchone()[0]
            is None
        )
        row = conn.execute(
            "SELECT narrative_md, sections_json FROM daily_summaries WHERE working_day_id = ?",
            (day_id,),
        ).fetchone()
        narrative = str(row["narrative_md"])
        sections = json.loads(str(row["sections_json"]))
        assert [item["title"] for item in sections["deals"]] == [
            "Acme Q4 proposal",
            "Globex pilot",
        ]
        acme_block = narrative.split("## Globex pilot")[0]
        globex_block = narrative.split("## Globex pilot")[1]
        assert f"[c:{acme_claim}]" in acme_block
        assert f"[c:{globex_claim}]" not in acme_block
        assert f"[c:{globex_claim}]" in globex_block
        assert f"[c:{acme_claim}]" not in globex_block
        by_deal = {item["deal_id"]: item for item in sections["slots"]}
        assert by_deal[acme_id]["slot"] == "next_step"
        assert by_deal[acme_id]["claim_ids"] == [acme_claim]
        assert by_deal[globex_id]["slot"] == "next_step"
        assert by_deal[globex_id]["claim_ids"] == [globex_claim]
    finally:
        conn.close()


def test_deal_fallback_does_not_pull_the_other_deal(tmp_path: Path) -> None:
    from tests.unit.test_consolidation import _insert_day

    conn = _open(tmp_path)
    try:
        day_id = _insert_day(conn)
        acme_frame = _insert_frame(conn, day_id, captured_at="2026-10-31T09:00:00Z")
        globex_frame = _insert_frame(conn, day_id, captured_at="2026-10-31T10:00:00Z")
        acme_seg = _insert_segment(
            conn, day_id, acme_frame, started_at="2026-10-31T09:00:00Z", merged="Acme mail"
        )
        globex_seg = _insert_segment(
            conn, day_id, globex_frame, started_at="2026-10-31T10:00:00Z", merged="Globex mail"
        )
        acme_obs = _insert_observation(conn, acme_seg, description="Emailing Acme")
        globex_obs = _insert_observation(conn, globex_seg, description="Emailing Globex")
        acme_claim = _insert_claim(
            conn,
            acme_obs,
            day_id,
            acme_seg,
            acme_frame,
            kind="activity",
            assertion="observed",
            text="Sent Acme pricing",
            created_at="2026-10-31T09:00:01Z",
        )
        globex_claim = _insert_claim(
            conn,
            globex_obs,
            day_id,
            globex_seg,
            globex_frame,
            kind="activity",
            assertion="observed",
            text="Sent Globex intro",
            created_at="2026-10-31T10:00:01Z",
        )
        runtime = FakeRuntime(
            recordings={
                "complete": [
                    {
                        "match": "Cluster these observations",
                        "response": {
                            "deals": [
                                {
                                    "title": "Acme Q4 proposal",
                                    "summary": "Acme thread",
                                    "observation_indexes": [1],
                                },
                                {
                                    "title": "Globex pilot",
                                    "summary": "Globex thread",
                                    "observation_indexes": [2],
                                },
                            ]
                        },
                    },
                    {
                        "match": "Extract cross-segment",
                        "response": {
                            "entity_deltas": [],
                            "decisions": [],
                            "commitments": [],
                        },
                    },
                    {
                        "match": "Write the daily narrative",
                        "response": {
                            "paragraphs": [
                                {
                                    "text": "Ghost [c:99].",
                                    "claim_ids": ["99"],
                                    "slot": "what_happened",
                                },
                                {
                                    "text": f"What happened: Sent Globex intro [c:{globex_claim}].",
                                    "claim_ids": [globex_claim],
                                    "slot": "what_happened",
                                },
                            ]
                        },
                    },
                ]
            }
        )
        consolidate_working_day(conn, day_id, runtime=runtime, embedding=runtime, now=NOW)
        narrative = str(
            conn.execute(
                "SELECT narrative_md FROM daily_summaries WHERE working_day_id = ?",
                (day_id,),
            ).fetchone()[0]
        )
        acme_block = narrative.split("## Globex pilot")[0]
        assert f"[c:{acme_claim}]" in acme_block
        assert f"[c:{globex_claim}]" not in acme_block
        assert "Sent Globex intro" not in acme_block
    finally:
        conn.close()


def test_stored_heading_uses_deal_title_not_model_heading(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(
            recordings={
                "complete": [
                    {
                        "match": "Cluster these observations",
                        "response": {
                            "deals": [
                                {
                                    "title": "Acme Q4 proposal",
                                    "summary": "same opportunity",
                                    "observation_indexes": [1, 2, 3],
                                }
                            ]
                        },
                    },
                    {
                        "match": "Extract cross-segment",
                        "response": {
                            "entity_deltas": [],
                            "decisions": [],
                            "commitments": [],
                        },
                    },
                    {
                        "match": "Write the daily narrative",
                        "response": {
                            "paragraphs": [
                                {
                                    "text": (
                                        "## Model heading\nWhat happened: "
                                        f"Sent the pricing pack [c:{seeded['sent']}]."
                                    ),
                                    "claim_ids": [seeded["sent"]],
                                    "slot": "what_happened",
                                }
                            ]
                        },
                    },
                ]
            }
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        narrative = str(
            conn.execute(
                "SELECT narrative_md FROM daily_summaries WHERE working_day_id = ?",
                (seeded["day_id"],),
            ).fetchone()[0]
        )
        assert narrative.startswith("## Acme Q4 proposal")
        assert "## Model heading" not in narrative
    finally:
        conn.close()

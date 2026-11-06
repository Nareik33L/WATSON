"""M3f deal grouping and per-deal daily summary (plan §3.7 / D3 / D8)."""

from __future__ import annotations

import functools
import io
import json
import logging
from pathlib import Path

import pytest
import yaml

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
from watson.intelligence import prompts as prompt_mod
from watson.intelligence.eval.deals import load_deal_day
from watson.intelligence.prompts import load_prompt
from watson.intelligence.runtime import FakeRuntime
from watson.intelligence.runtime.types import OutputParseError
from watson.intelligence.schemas.consolidation import ClusteringOutput, SummaryParagraph
from watson.pipeline import consolidation as cons_mod
from watson.pipeline.consolidation import (
    _summarize_day,
    _summary_deal_sections,
    consolidate_working_day,
    derived_counts,
)
from watson.pipeline.understanding import understand_segment
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


def _assert_no_deal_summary_credit(conn: object, seeded: dict[str, str]) -> None:
    from watson.intelligence.eval.deals import (
        DayGold,
        Deal,
        DealCorpus,
        Frame,
        GoldFact,
        GoldSegment,
        Person,
        PredictedCorpus,
        predicted_day_from_collect,
        score_deals,
    )
    from watson.intelligence.eval.score import collect_predicted

    collected = collect_predicted(conn, seeded["day_id"])
    assert collected["summary_slots"] == []
    predicted = predicted_day_from_collect("day_seeded", collected)
    assert predicted.claims == []
    frames: dict[str, Frame] = {}
    gold_segments: list[GoldSegment] = []
    for row in collected["segment_deals"]:
        sid = str(row["segment_id"])
        fids = tuple(str(fid) for fid in row.get("frame_ids") or ())
        deal = "none" if sid == seeded["noise"] else "acme"
        gold_segments.append(GoldSegment(id=sid, frame_ids=fids, deal=deal))
        for fid in fids:
            frames[fid] = Frame(id=fid, text="seed")
    gold = DayGold(
        name="day_seeded",
        date="2026-10-31",
        deals=[Deal(id="acme", name="Acme Q4 proposal", company="Acme")],
        people=[Person(id="priya", name="Priya Natarajan")],
        segments=gold_segments,
        frames=frames,
        facts=[
            GoldFact(
                "f-what",
                "acme",
                "what_happened",
                ("pricing", "pack"),
                {},
                (seeded["mail"],),
            ),
            GoldFact(
                "f-who",
                "acme",
                "who_involved",
                ("Priya",),
                {},
                (seeded["call"],),
                person_id="priya",
            ),
            GoldFact(
                "f-next",
                "acme",
                "next_step",
                ("revised", "pricing"),
                {},
                (seeded["doc"],),
            ),
        ],
        skipped={},
    )
    score = score_deals(DealCorpus(days=[gold]), PredictedCorpus(days=[predicted]))
    assert score.summary.claims == 0
    assert score.summary.accuracy < 0.85
    assert score.summary.accuracy == 0.0


def _score_seeded_summary(conn: object, seeded: dict[str, str]) -> tuple[dict[str, object], object]:
    """Score stored slots against gold that the upstream claim rows would cover."""
    from watson.intelligence.eval.deals import (
        DayGold,
        Deal,
        DealCorpus,
        Frame,
        GoldFact,
        GoldSegment,
        Person,
        PredictedCorpus,
        predicted_day_from_collect,
        score_deals,
    )
    from watson.intelligence.eval.score import collect_predicted

    collected = collect_predicted(conn, seeded["day_id"])
    for row in collected["segment_deals"]:
        if row.get("frame_ids"):
            continue
        start = conn.execute(
            "SELECT start_frame_id FROM segments WHERE id = ?",
            (row["segment_id"],),
        ).fetchone()
        if start and start[0]:
            row["frame_ids"] = [str(start[0])]
    predicted = predicted_day_from_collect("day_seeded", collected)
    frames: dict[str, Frame] = {}
    gold_segments: list[GoldSegment] = []
    ocr = "Priya sent the pricing pack. Kieran will send revised pricing."
    for row in collected["segment_deals"]:
        sid = str(row["segment_id"])
        fids = tuple(str(fid) for fid in row.get("frame_ids") or ())
        deal = "none" if sid == seeded["noise"] else "acme"
        gold_segments.append(GoldSegment(id=sid, frame_ids=fids, deal=deal))
        for fid in fids:
            frames[fid] = Frame(id=fid, text=ocr)
    gold = DayGold(
        name="day_seeded",
        date="2026-10-31",
        deals=[Deal(id="acme", name="Acme Q4 proposal", company="Acme")],
        people=[Person(id="priya", name="Priya Natarajan")],
        segments=gold_segments,
        frames=frames,
        facts=[
            GoldFact(
                "f-what",
                "acme",
                "what_happened",
                ("pricing", "pack"),
                {},
                (seeded["mail"],),
            ),
            GoldFact(
                "f-who",
                "acme",
                "who_involved",
                ("Priya",),
                {},
                (seeded["call"],),
            ),
            GoldFact(
                "f-next",
                "acme",
                "next_step",
                ("revised", "pricing"),
                {},
                (seeded["doc"],),
            ),
        ],
        skipped={},
    )
    return collected, score_deals(DealCorpus(days=[gold]), PredictedCorpus(days=[predicted]))


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


_PROMPT_ROOT = Path(prompt_mod.__file__).resolve().parent
_SKIP_GOLD_TOKENS = frozenset({"none", "missing", "n/a", "na"})


def _fixture_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "tests" / "fixtures"
        if candidate.is_dir() and (candidate / "deal_gold").is_dir():
            return candidate
    raise FileNotFoundError("tests/fixtures not found")


def _gold_token(raw: object) -> str | None:
    token = " ".join(str(raw or "").split())
    if not token or token.isdigit() or token.casefold() in _SKIP_GOLD_TOKENS:
        return None
    distinctive = any(ch in "$£€" for ch in token) or (
        any(ch.isdigit() for ch in token) and any(ch.isalpha() for ch in token)
    )
    if len(token) < 4 and not distinctive:
        return None
    return token


def _add_gold(bucket: set[str], raw: object) -> None:
    token = _gold_token(raw)
    if token:
        bucket.add(token)


def _add_fact_strings(strings: set[str], required_terms: object, aliases: object) -> None:
    terms = [str(item) for item in list(required_terms or []) if str(item).strip()]
    for term in terms:
        token = _gold_token(term)
        if token and (len(token) >= 8 or any(ch.isdigit() or ch in "$£€ " for ch in token)):
            strings.add(token)
    if len(terms) >= 2:
        _add_gold(strings, " ".join(terms))
    if isinstance(aliases, dict):
        for phrases in aliases.values():
            for phrase in list(phrases or []):
                _add_gold(strings, phrase)


def _add_distinctive_phrases(strings: set[str], text: object) -> None:
    body = " ".join(str(text or "").split())
    _add_gold(strings, body)
    words = body.split()
    for size in (2, 3, 4):
        for index in range(0, len(words) - size + 1):
            chunk = " ".join(words[index : index + size])
            if any(ch.isdigit() or ch in "$£€" for ch in chunk):
                _add_gold(strings, chunk)


def _add_gold_sentence(strings: set[str], text: object) -> None:
    """Full gold sentence plus 4+ word spans so truncated plants still match."""
    body = " ".join(str(text or "").split())
    _add_gold(strings, body)
    stripped = body.rstrip(".,;: ")
    _add_gold(strings, stripped)
    words = stripped.split()
    for size in range(4, len(words) + 1):
        for index in range(0, len(words) - size + 1):
            _add_gold(strings, " ".join(words[index : index + size]))


def _collect_replay_truth(names: set[str], strings: set[str]) -> None:
    root = _fixture_root()
    paths = sorted(root.glob("day_*/truth.yaml"))
    assert paths, "expected replay tests/fixtures/day_*/truth.yaml"
    for path in paths:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for project in payload.get("projects") or []:
            _add_gold(names, project)
            _add_gold(strings, project)
        for key in ("decisions", "commitments"):
            for row in payload.get(key) or []:
                text = row.get("text") if isinstance(row, dict) else row
                _add_gold_sentence(strings, text)


def _collect_from_mapping(
    payload: object, names: set[str], ids: set[str], strings: set[str]
) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_s = str(key)
            if key_s in {"id", "person_id"} or key_s.endswith("_id") or key_s.endswith("_ids"):
                if isinstance(value, list):
                    for item in value:
                        _add_gold(ids, item)
                else:
                    _add_gold(ids, value)
            elif key_s in {"name", "company"}:
                _add_gold(names, value)
            elif key_s == "required_terms":
                _add_fact_strings(strings, value, payload.get("aliases"))
            elif key_s == "aliases":
                _add_fact_strings(strings, payload.get("required_terms"), value)
            elif key_s == "text":
                _add_distinctive_phrases(strings, value)
            else:
                _collect_from_mapping(value, names, ids, strings)
    elif isinstance(payload, list):
        for item in payload:
            _collect_from_mapping(item, names, ids, strings)


def _collect_from_day(day: object, names: set[str], ids: set[str], strings: set[str]) -> None:
    _add_gold(ids, getattr(day, "name", None))
    for deal in getattr(day, "deals", []) or []:
        _add_gold(names, getattr(deal, "name", None))
        _add_gold(names, getattr(deal, "company", None))
        _add_gold(ids, getattr(deal, "id", None))
    for person in getattr(day, "people", []) or []:
        _add_gold(names, getattr(person, "name", None))
        _add_gold(names, getattr(person, "company", None))
        _add_gold(ids, getattr(person, "id", None))
    for segment in getattr(day, "segments", []) or []:
        _add_gold(ids, getattr(segment, "id", None))
        for frame_id in getattr(segment, "frame_ids", ()) or ():
            _add_gold(ids, frame_id)
    for fact in getattr(day, "facts", []) or []:
        _add_gold(ids, getattr(fact, "id", None))
        _add_gold(ids, getattr(fact, "person_id", None))
        _add_fact_strings(
            strings, getattr(fact, "required_terms", ()), getattr(fact, "aliases", {})
        )
        for segment_id in getattr(fact, "evidence_segments", ()) or ():
            _add_gold(ids, segment_id)
    for frame_id in getattr(day, "frames", {}) or {}:
        _add_gold(ids, frame_id)
    for frame_id in getattr(day, "skipped", {}) or {}:
        _add_gold(ids, frame_id)


@functools.lru_cache(maxsize=1)
def _gold_leak_tokens() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Auto-discovered gold names, ids, and strings that must not leak into prompts."""
    names: set[str] = set()
    ids: set[str] = set()
    strings: set[str] = set()
    root = _fixture_root()
    gold_dirs = sorted({path.parent for path in (root / "deal_gold").glob("*/truth.yaml")})
    overlay_paths = sorted(root.glob("day_*/deals.yaml"))
    honest_paths = sorted((root / "deal_gold").glob("*/honest_summary.yaml"))
    assert gold_dirs and overlay_paths and honest_paths
    for day_dir in gold_dirs:
        _collect_from_day(load_deal_day(day_dir), names, ids, strings)
        _collect_from_mapping(
            yaml.safe_load((day_dir / "truth.yaml").read_text(encoding="utf-8")),
            names,
            ids,
            strings,
        )
    for overlay in overlay_paths:
        _collect_from_mapping(
            yaml.safe_load(overlay.read_text(encoding="utf-8")), names, ids, strings
        )
    for honest in honest_paths:
        _collect_from_mapping(
            yaml.safe_load(honest.read_text(encoding="utf-8")), names, ids, strings
        )
    _collect_replay_truth(names, strings)
    assert names and ids and strings
    return frozenset(names), frozenset(ids), frozenset(strings)


def _template_needles() -> set[str]:
    names, ids, strings = _gold_leak_tokens()
    return set(names) | set(ids) | set(strings)


def _deal_names() -> set[str]:
    names: set[str] = set()
    root = _fixture_root()
    for path in [*(root / "deal_gold").glob("*/truth.yaml"), *root.glob("day_*/deals.yaml")]:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for row in payload.get("deals") or []:
            if isinstance(row, dict):
                _add_gold(names, row.get("name"))
    assert names
    return names


def _leaked(blob: str, needles: set[str]) -> list[str]:
    folded = blob.casefold()
    return [
        needle for needle in sorted(needles, key=len, reverse=True) if needle.casefold() in folded
    ]


def _absent_from(needles: set[str], blob: str) -> set[str]:
    folded = blob.casefold()
    return {needle for needle in needles if needle.casefold() not in folded}


def _all_prompt_templates() -> list[tuple[str, str]]:
    paths = sorted(_PROMPT_ROOT.rglob("*.md"))
    assert paths, "expected prompt templates under intelligence/prompts"
    return [
        (str(path.relative_to(_PROMPT_ROOT)), path.read_text(encoding="utf-8")) for path in paths
    ]


def _built_prompt_constants() -> list[tuple[str, str]]:
    found = [
        (name, value)
        for name, value in sorted(vars(cons_mod).items())
        if name.endswith("_USER_PREFIX") and isinstance(value, str) and value.strip()
    ]
    assert found, "expected *_USER_PREFIX constants on consolidation"
    return found


def _prefix_matching(marker: str) -> str:
    for _name, value in _built_prompt_constants():
        if marker.casefold() in value.casefold():
            return value
    raise AssertionError(f"no user prefix contains {marker!r}")


def _seeded_input_blob(conn: object, day_id: str) -> str:
    parts: list[str] = []
    for row in conn.execute(  # type: ignore[union-attr]
        """
        SELECT o.description, o.raw_json
        FROM observations o
        JOIN segments s ON s.id = o.segment_id
        WHERE s.working_day_id = ?
        """,
        (day_id,),
    ):
        parts.append(str(row["description"] or ""))
        parts.append(str(row["raw_json"] or ""))
    for row in conn.execute(  # type: ignore[union-attr]
        "SELECT text FROM claims WHERE working_day_id = ?",
        (day_id,),
    ):
        parts.append(str(row["text"] or ""))
    for row in conn.execute(  # type: ignore[union-attr]
        "SELECT merged_text, window_title_norm FROM segments WHERE working_day_id = ?",
        (day_id,),
    ):
        parts.append(str(row["merged_text"] or ""))
        parts.append(str(row["window_title_norm"] or ""))
    for row in conn.execute("SELECT canonical_name FROM entities"):  # type: ignore[union-attr]
        parts.append(str(row["canonical_name"] or ""))
    return "\n".join(parts)


def _capture_runtime(
    captured: list[str], recordings: dict[str, object] | None = None
) -> FakeRuntime:
    class CaptureRuntime(FakeRuntime):
        def complete(self, messages, schema, *, num_predict=None):  # type: ignore[no-untyped-def]
            captured.append("\n".join(item["content"] for item in messages))
            return super().complete(messages, schema, num_predict=num_predict)

    return CaptureRuntime(recordings=recordings)


def _capture_built_prompts(tmp_path: Path) -> tuple[object, dict[str, str], list[str]]:
    conn = _open(tmp_path)
    seeded = _seed_channels(conn)
    captured: list[str] = []
    runtime = _capture_runtime(captured, _DEAL_RECORDINGS)
    consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
    _summarize_day(
        runtime,
        [
            {
                "id": seeded["sent"],
                "kind": "activity",
                "assertion": "observed",
                "text": "Sent the pricing pack",
            }
        ],
        load_prompt("daily_summary", "v2").text,
        deal_groups=None,
        roster=None,
    )
    extra = _insert_frame(conn, seeded["day_id"], captured_at="2026-10-31T13:00:00Z")
    extra_seg = _insert_segment(
        conn,
        seeded["day_id"],
        extra,
        started_at="2026-10-31T13:00:00Z",
        merged="Inbox glance",
    )
    understand_segment(conn, extra_seg, runtime, runtime)
    assert captured
    return conn, seeded, captured


def _summary_prompts(captured: list[str]) -> list[str]:
    return [blob for blob in captured if "Write the daily narrative" in blob]


def _no_deal_summary_prompts(captured: list[str]) -> list[str]:
    return [blob for blob in _summary_prompts(captured) if "Deal:" not in blob]


def _understanding_prompts(captured: list[str]) -> list[str]:
    return [blob for blob in captured if "Merged text (capped at" in blob]


def _roster_lines(blob: str) -> list[str]:
    return [line for line in blob.splitlines() if line.casefold().startswith("roster names:")]


def test_prompt_templates_and_built_prompts_omit_gold_fixtures(tmp_path: Path) -> None:
    needles = _template_needles()
    for label, blob in (*_all_prompt_templates(), *_built_prompt_constants()):
        hits = _leaked(blob, needles)
        assert not hits, f"{label} leaked gold fixtures: {hits}"

    conn, seeded, captured = _capture_built_prompts(tmp_path)
    try:
        seeded_blob = _seeded_input_blob(conn, seeded["day_id"])
        forbidden = _absent_from(needles, seeded_blob)
        deal_names = _deal_names()
        assert any("Deal:" in blob for blob in _summary_prompts(captured))
        assert _no_deal_summary_prompts(captured)
        assert _understanding_prompts(captured)
        for blob in captured:
            hits = _leaked(blob, forbidden)
            assert not hits, f"built prompt leaked gold not in seeded inputs: {hits}"
            for line in _roster_lines(blob):
                roster_hits = _leaked(line, deal_names)
                assert not roster_hits, f"roster_line leaked deal titles: {roster_hits}"
    finally:
        conn.close()


def test_leakage_guard_flags_person_name_in_daily_summary_template() -> None:
    needles = _template_needles()
    planted = "Priya Natarajan"
    assert planted in _gold_leak_tokens()[0]
    template = load_prompt("daily_summary", "v2").text
    assert not _leaked(template, needles)
    assert planted in _leaked(template + f"\n{planted}", needles)


def test_leakage_guard_flags_gold_id_in_clustering_prefix() -> None:
    needles = _template_needles()
    planted = "fact-q4-call"
    assert planted in _gold_leak_tokens()[1]
    prefix = _prefix_matching("Cluster these observations")
    assert not _leaked(prefix, needles)
    assert planted in _leaked(prefix + planted, needles)


def test_leakage_guard_flags_gold_fact_strings_in_extract_prefix_and_templates() -> None:
    needles = _template_needles()
    planted = next(
        text
        for text in sorted(_gold_leak_tokens()[2], key=len, reverse=True)
        if "14 nov demo" in text.casefold() and text.casefold().startswith("noted")
    )
    prefix = _prefix_matching("Extract cross-segment")
    assert planted in _leaked(prefix + f"\n{planted}", needles)
    for label, blob in _all_prompt_templates():
        assert planted in _leaked(blob + f"\n{planted}", needles), label


def test_leakage_guard_flags_alias_in_clustering_prefix() -> None:
    needles = _template_needles()
    planted = "14 Nov demo"
    prefix = _prefix_matching("Cluster these observations")
    assert not _leaked(prefix, needles)
    hits = _leaked(prefix + planted, needles)
    assert hits, f"alias {planted!r} must fail the guard"


def test_leakage_guard_flags_gold_name_only_in_built_summary_prompt(tmp_path: Path) -> None:
    names, _ids, strings = _gold_leak_tokens()
    conn, seeded, captured = _capture_built_prompts(tmp_path)
    try:
        seeded_blob = _seeded_input_blob(conn, seeded["day_id"])
        planted = next(
            name
            for name in sorted(names, key=len, reverse=True)
            if " " in name and name.casefold() not in seeded_blob.casefold()
        )
        summary = next(blob for blob in captured if "Write the daily narrative" in blob)
        forbidden = _absent_from(set(names) | set(strings), seeded_blob)
        assert planted in forbidden
        assert not _leaked(summary, forbidden)
        assert planted in _leaked(summary + f"\n{planted}", forbidden)
    finally:
        conn.close()


def test_leakage_guard_flags_deal_title_in_roster_line() -> None:
    deal_names = _deal_names()
    planted = next(name for name in sorted(deal_names, key=len, reverse=True))
    line = "Roster names: Priya Natarajan"
    assert not _leaked(line, deal_names)
    assert planted in _leaked(f"{line}, {planted}", deal_names)


def test_leakage_guard_flags_replay_gold_ids() -> None:
    needles = _template_needles()
    ids = _gold_leak_tokens()[1]
    planted = next(item for item in sorted(ids) if item.startswith("fact-v1-"))
    prefix = _prefix_matching("Cluster these observations")
    assert planted in ids
    assert not _leaked(prefix, needles)
    assert planted in _leaked(prefix + planted, needles)
    for label, blob in _all_prompt_templates():
        assert planted in _leaked(blob + f"\n{planted}", needles), label


def test_leakage_guard_flags_gold_name_in_no_deal_summary_prompt(tmp_path: Path) -> None:
    names, _ids, strings = _gold_leak_tokens()
    conn, seeded, captured = _capture_built_prompts(tmp_path)
    try:
        seeded_blob = _seeded_input_blob(conn, seeded["day_id"])
        planted = next(
            name
            for name in sorted(names, key=len, reverse=True)
            if " " in name and name.casefold() not in seeded_blob.casefold()
        )
        no_deal = _no_deal_summary_prompts(captured)
        assert no_deal
        forbidden = _absent_from(set(names) | set(strings), seeded_blob)
        assert planted in forbidden
        for blob in no_deal:
            assert not _leaked(blob, forbidden)
            assert planted in _leaked(blob + f"\n{planted}", forbidden)
    finally:
        conn.close()


def test_leakage_guard_flags_replay_decision_commitment_project_strings() -> None:
    needles = _template_needles()
    planted = "Adopt the published three-tier rate card."
    assert _leaked(planted, needles)
    prefix = _prefix_matching("Extract cross-segment")
    assert not _leaked(prefix, needles)
    assert _leaked(prefix + planted, needles)
    names, _ids, strings = _gold_leak_tokens()
    project = "Internal Onboarding Guide"
    assert project in names or project in strings
    assert project in _leaked(prefix + project, needles)


def test_leakage_guard_flags_gold_string_in_understanding_prompt(tmp_path: Path) -> None:
    needles = _template_needles()
    planted = "Adopt the published three-tier rate card."
    conn, seeded, captured = _capture_built_prompts(tmp_path)
    try:
        understanding = _understanding_prompts(captured)
        assert understanding
        forbidden = _absent_from(needles, _seeded_input_blob(conn, seeded["day_id"]))
        assert _leaked(planted, forbidden)
        for blob in understanding:
            assert not _leaked(blob, forbidden)
            assert _leaked(blob + f"\n{planted}", forbidden)
    finally:
        conn.close()


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


def test_invented_cited_paragraph_cannot_score_full_credit(tmp_path: Path) -> None:
    invented = [
        "What happened: Northwind acquired Contoso for £100 billion [c:1].",
        "Who involved: The Northwind board [c:2].",
        "Next step: Announce the merger tomorrow [c:3].",
    ]
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(
            recordings=_recordings_with_summary(
                [
                    {"text": invented[0], "claim_ids": ["1"], "slot": "what_happened"},
                    {"text": invented[1], "claim_ids": ["2"], "slot": "who_involved"},
                    {"text": invented[2], "claim_ids": ["3"], "slot": "next_step"},
                ]
            )
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        narrative = str(
            conn.execute(
                "SELECT narrative_md FROM daily_summaries WHERE working_day_id = ?",
                (seeded["day_id"],),
            ).fetchone()[0]
        )
        assert "Northwind acquired Contoso" in narrative
        assert f"[c:{seeded['sent']}]" in narrative
        assert f"[c:{seeded['who']}]" in narrative
        assert f"[c:{seeded['next']}]" in narrative
        collected, score = _score_seeded_summary(conn, seeded)
        shown = {cid: str(info.get("text") or "") for cid, info in collected["deal_claims"].items()}
        assert shown[seeded["sent"]] == (
            "What happened: Northwind acquired Contoso for £100 billion."
        )
        assert shown[seeded["who"]] == "Who involved: The Northwind board."
        assert shown[seeded["next"]] == "Next step: Announce the merger tomorrow."
        assert all("pricing pack" not in text.casefold() for text in shown.values())
        assert all("priya joined" not in text.casefold() for text in shown.values())
        assert score.summary.claims == 3
        assert score.summary.accuracy < 0.85
        assert score.summary.accuracy != 1.0
        assert score.summary.correct == 0
    finally:
        conn.close()


def test_honest_cited_paragraph_still_scores_deal_summary(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(recordings=_DEAL_RECORDINGS)
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        collected, score = _score_seeded_summary(conn, seeded)
        shown = {cid: str(info.get("text") or "") for cid, info in collected["deal_claims"].items()}
        assert "pricing pack" in shown[seeded["sent"]].casefold()
        assert "priya" in shown[seeded["who"]].casefold()
        assert "revised pricing" in shown[seeded["next"]].casefold()
        assert score.summary.accuracy >= 0.85
        assert score.summary.accuracy == 1.0
    finally:
        conn.close()


def test_honest_then_invented_same_cite_does_not_score(tmp_path: Path) -> None:
    """Every displayed sentence that cites a claim must cover; extras cannot ride."""
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(
            recordings=_recordings_with_summary(
                [
                    {
                        "text": (
                            "What happened: Sent the pricing pack [c:1]. "
                            "Northwind acquired Contoso for £100 billion [c:1]."
                        ),
                        "claim_ids": ["1"],
                        "slot": "what_happened",
                    },
                    {
                        "text": (
                            "Who involved: Priya joined the call [c:2]. The Northwind board [c:2]."
                        ),
                        "claim_ids": ["2"],
                        "slot": "who_involved",
                    },
                    {
                        "text": (
                            "Next step: Kieran will send revised pricing [c:3]. "
                            "Announce the merger tomorrow [c:3]."
                        ),
                        "claim_ids": ["3"],
                        "slot": "next_step",
                    },
                ]
            )
        )
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        collected, score = _score_seeded_summary(conn, seeded)
        shown = {cid: str(info.get("text") or "") for cid, info in collected["deal_claims"].items()}
        assert "pricing pack" in shown[seeded["sent"]].casefold()
        assert "northwind acquired contoso" in shown[seeded["sent"]].casefold()
        assert "priya" in shown[seeded["who"]].casefold()
        assert "northwind board" in shown[seeded["who"]].casefold()
        assert "revised pricing" in shown[seeded["next"]].casefold()
        assert "announce the merger" in shown[seeded["next"]].casefold()
        assert score.summary.claims == 3
        assert score.summary.accuracy == 0.0
        assert score.summary.correct == 0
    finally:
        conn.close()


def _overwrite_displayed_summary(conn: object, day_id: str, body: str) -> None:
    row = conn.execute(
        "SELECT sections_json FROM daily_summaries WHERE working_day_id = ?",
        (day_id,),
    ).fetchone()
    sections = json.loads(str(row[0]))
    for para in sections.get("paragraphs") or []:
        if not isinstance(para, dict):
            continue
        if str(para.get("source") or "model") == "fallback":
            continue
        para["text"] = body
    conn.execute(
        """
        UPDATE daily_summaries
        SET narrative_md = ?, sections_json = ?
        WHERE working_day_id = ?
        """,
        (body, json.dumps(sections), day_id),
    )
    conn.commit()


@pytest.mark.parametrize(
    "shape",
    ("citation-only", "heading-only", "citation-after-the-period"),
)
def test_nothing_displayed_paragraphs_score_zero(tmp_path: Path, shape: str) -> None:
    """Citation-only / heading-only / cite-after-period must not read claims.text."""
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(recordings=_DEAL_RECORDINGS)
        consolidate_working_day(conn, seeded["day_id"], runtime=runtime, embedding=runtime, now=NOW)
        sent, who, nxt = seeded["sent"], seeded["who"], seeded["next"]
        title = "Acme Q4 proposal"
        bodies = {
            "citation-only": f"## {title}\n\n[c:{sent}]. [c:{who}]. [c:{nxt}].",
            "heading-only": f"## {title} [c:{sent}] [c:{who}] [c:{nxt}]",
            "citation-after-the-period": (
                f"## {title}\n\n"
                f"Sent the pricing pack. [c:{sent}]. "
                f"Priya joined the call. [c:{who}]. "
                f"Kieran will send revised pricing. [c:{nxt}]."
            ),
        }
        _overwrite_displayed_summary(conn, seeded["day_id"], bodies[shape])
        collected, score = _score_seeded_summary(conn, seeded)
        shown = {cid: str(info.get("text") or "") for cid, info in collected["deal_claims"].items()}
        assert set(shown) == {sent, who, nxt}
        assert all(text == "" for text in shown.values())
        assert all("pricing pack" not in text.casefold() for text in shown.values())
        assert all("priya joined" not in text.casefold() for text in shown.values())
        assert all("revised pricing" not in text.casefold() for text in shown.values())
        assert score.summary.claims == 3
        assert score.summary.accuracy == 0.0
        assert score.summary.correct == 0
    finally:
        conn.close()


def test_invented_claim_ids_only_text_is_not_cited_as_model(tmp_path: Path) -> None:
    invented = "Northwind acquired Contoso for £100 billion"
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(
            recordings=_recordings_with_summary(
                [
                    {
                        "text": invented,
                        "claim_ids": ["1", "2", "3"],
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
        assert invented not in narrative
        assert all(item.get("source") == "fallback" for item in sections["slots"])
        assert all(item.get("source") == "fallback" for item in sections.get("paragraphs") or [])
        _assert_no_deal_summary_credit(conn, seeded)
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
        assert slots["who_involved"]["source"] == "model"
        assert all(
            not any(str(cid).isdigit() for cid in item["claim_ids"]) for item in sections["slots"]
        )
    finally:
        conn.close()


def test_dropped_next_step_prefix_does_not_credit_next_step(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    try:
        seeded = _seed_channels(conn)
        runtime = FakeRuntime(
            recordings=_recordings_with_summary(
                [
                    {
                        "text": (
                            "Next step: invent a merger. "
                            "What happened: Sent the pricing pack [c:1]."
                        ),
                        "claim_ids": ["1"],
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
        slots = {item["slot"]: item for item in sections["slots"]}
        assert "invent a merger" not in narrative
        assert "next_step" not in slots
        assert slots["what_happened"]["claim_ids"] == [seeded["sent"]]
        assert slots["what_happened"]["source"] == "model"
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
        assert all(item.get("source") == "fallback" for item in sections["slots"])
        assert all(item.get("source") == "fallback" for item in sections.get("paragraphs") or [])
        assert all(item.get("deal_id") not in {"", "none"} for item in sections["slots"])
        assert all(
            not any(str(cid).isdigit() for cid in item["claim_ids"]) for item in sections["slots"]
        )
        cited = {cid for item in sections["slots"] for cid in item["claim_ids"]}
        assert seeded["noise_claim"] not in cited
        assert f"[c:{seeded['sent']}]" in narrative
        assert f"[c:{seeded['who']}]" in narrative
        assert f"[c:{seeded['next']}]" in narrative
        _assert_no_deal_summary_credit(conn, seeded)
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
        assert all(item.get("source") == "fallback" for item in sections["slots"])
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
        {
            "slot": "next_step",
            "claim_ids": ["claim-uuid"],
            "deal_id": "deal-1",
            "source": "model",
        }
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

"""Unit coverage for Rev3 deal grouping, summary, and #46 tightenings."""

from __future__ import annotations

import pytest

from watson.intelligence.eval.deals import (
    CONTENT_TOKEN_CAP,
    MISSING,
    NONE,
    DayGold,
    Deal,
    DealCorpus,
    Frame,
    GoldFact,
    GoldSegment,
    Person,
    PredictedClaim,
    PredictedCorpus,
    PredictedDay,
    PredictedEvidence,
    PredictedSegment,
    align_segments,
    content_tokens,
    contradicts,
    dump_windows,
    extract_dates,
    has_phrase,
    match_clusters,
    person_entities_match,
    privacy_leak,
    required_terms_present,
    resolve_person_id,
    score_deals,
    slot_modal_ok,
    validate_day,
)
from watson.intelligence.eval.metrics import normalize


def _day(
    *,
    name: str = "day_x",
    deals: list[Deal] | None = None,
    people: list[Person] | None = None,
    segments: list[GoldSegment] | None = None,
    frames: dict[str, Frame] | None = None,
    facts: list[GoldFact] | None = None,
    skipped: dict[str, Frame] | None = None,
) -> DayGold:
    return DayGold(
        name=name,
        date="2026-01-01",
        deals=deals or [],
        people=people or [],
        segments=segments or [],
        frames=frames or {},
        facts=facts or [],
        skipped=skipped or {},
    )


def _seg(
    seg_id: str,
    deal: str,
    *frames: str,
    title: str = "",
    bundle: str = "com.lumen.mail",
    bait: bool = False,
    started: str = "",
) -> GoldSegment:
    return GoldSegment(
        id=seg_id,
        frame_ids=frames,
        deal=deal,
        bundle_id=bundle,
        window_title=title,
        started_at=started or f"2026-01-01T0{seg_id[-1] if seg_id[-1].isdigit() else 1}:00:00Z",
        privacy_bait=bait,
    )


def test_greedy_max_overlap_then_gold_id() -> None:
    day = _day(
        deals=[Deal("a", "A"), Deal("b", "B")],
        segments=[
            _seg("s1", "a", "f1"),
            _seg("s2", "a", "f2"),
            _seg("s3", "b", "f3"),
            _seg("s4", "b", "f4"),
        ],
    )
    labels = {"s1": "p1", "s2": "p1", "s3": "p1", "s4": "p2"}
    mapping = match_clusters(day, labels)
    assert mapping["p1"] == "a"
    assert mapping["p2"] == "b"


def test_none_is_its_own_class() -> None:
    day = _day(
        deals=[Deal("a", "A")],
        segments=[_seg("s1", "a", "f1"), _seg("s2", NONE, "f2")],
        frames={"f1": Frame("f1", "hello"), "f2": Frame("f2", "noise")},
    )
    gold = DealCorpus(days=[day])
    predicted = PredictedCorpus(
        days=[
            PredictedDay(
                name="day_x",
                segments=[
                    PredictedSegment("s1", ("f1",), NONE),
                    PredictedSegment("s2", ("f2",), NONE),
                ],
                claims=[],
            )
        ]
    )
    score = score_deals(gold, predicted)
    assert score.grouping.deal_segment_recall == 0.0
    assert score.grouping.assignment_accuracy == 0.5


def test_pairwise_precision_zero_without_predicted_pairs() -> None:
    day = _day(
        deals=[Deal("a", "A")],
        segments=[_seg("s1", "a", "f1"), _seg("s2", "a", "f2")],
        frames={"f1": Frame("f1", "x"), "f2": Frame("f2", "y")},
    )
    gold = DealCorpus(days=[day])
    predicted = PredictedCorpus(
        days=[
            PredictedDay(
                name="day_x",
                segments=[
                    PredictedSegment("s1", ("f1",), "c1"),
                    PredictedSegment("s2", ("f2",), "c2"),
                ],
                claims=[],
            )
        ]
    )
    score = score_deals(gold, predicted)
    assert score.grouping.pairwise_precision == 0.0
    assert score.grouping.pairwise_fn == 1


def test_align_uncovered_is_missing() -> None:
    day = _day(segments=[_seg("s1", "a", "f1")])
    labels = align_segments(day, [])
    assert labels["s1"] == MISSING


def test_dump_windows_never_split_multiword_term() -> None:
    pad = [f"token{index:02d}" for index in range(23)]
    windows = dump_windows([*pad, "priya natarajan"])
    assert windows[0] == " ".join(pad)
    assert windows[1] == "priya natarajan"
    assert all("priya" not in window or "priya natarajan" in window for window in windows)


def test_weekday_is_not_a_calendar_date() -> None:
    assert extract_dates("friday") == {"weekday:friday"}
    assert extract_dates("14 Nov") == {"cal:11-14"}
    assert extract_dates("14 November") == {"cal:11-14"}
    assert extract_dates("2026-11-14") == {"cal:11-14"}
    assert extract_dates("friday") != extract_dates("14 nov")


def test_person_entities_match_rejects_first_name_and_substring() -> None:
    people = [
        Person("priya-natarajan", "Priya Natarajan", "Acme"),
        Person("priya-chen", "Priya Chen", "Globex"),
    ]
    assert person_entities_match("Priya Natarajan", "priya-natarajan", people)
    assert not person_entities_match("Priya", "priya-natarajan", people)
    assert not person_entities_match("Natarajan", "priya-natarajan", people)
    assert resolve_person_id("Priya", people) is None


def test_shared_full_name_needs_company() -> None:
    people = [
        Person("jordan-acme", "Jordan Lee", "Acme"),
        Person("jordan-globex", "Jordan Lee", "Globex"),
    ]
    assert resolve_person_id("Jordan Lee", people) is None
    assert resolve_person_id("Jordan Lee Acme", people) == "jordan-acme"


def test_what_happened_rejects_will_and_should() -> None:
    fact = GoldFact("f", "d", "what_happened", ("pricing", "call"), {}, ("s",))
    will = PredictedClaim("c", "d", "what_happened", "will discuss pricing call")
    should = PredictedClaim("c2", "d", "what_happened", "should finish the pricing call")
    past = PredictedClaim("c3", "d", "what_happened", "joined the pricing call")
    assert slot_modal_ok(will, fact) is False
    assert slot_modal_ok(should, fact) is False
    assert slot_modal_ok(past, fact) is True


def test_next_step_rejects_may_and_sent() -> None:
    fact = GoldFact("f", "d", "next_step", ("send", "pricing"), {}, ("s",))
    may = PredictedClaim("c", "d", "next_step", "may send pricing")
    sent = PredictedClaim("c2", "d", "next_step", "sent pricing")
    will = PredictedClaim("c3", "d", "next_step", "will send pricing")
    assert slot_modal_ok(may, fact) is False
    assert slot_modal_ok(sent, fact) is False
    assert slot_modal_ok(will, fact) is True


def test_same_slot_person_swap_contradicts() -> None:
    people = [
        Person("priya-natarajan", "Priya Natarajan", "Acme"),
        Person("priya-chen", "Priya Chen", "Globex"),
    ]
    fact = GoldFact(
        "f",
        "acme-q4",
        "who_involved",
        (),
        {},
        ("s-mail",),
        person_id="priya-natarajan",
    )
    day = _day(
        people=people,
        deals=[Deal("acme-q4", "Acme Q4", "Acme")],
        facts=[fact],
    )
    claim = PredictedClaim("c", "acme-q4", "who_involved", "Priya Chen")
    assert contradicts(claim, fact, day, "Priya Natarajan emailed") is True


def test_extra_deal_person_in_evidence_does_not_contradict() -> None:
    people = [Person("priya-natarajan", "Priya Natarajan", "Acme")]
    what = GoldFact("f1", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s-call",))
    who = GoldFact(
        "f2",
        "acme-q4",
        "who_involved",
        (),
        {},
        ("s-call",),
        person_id="priya-natarajan",
    )
    day = _day(
        people=people,
        deals=[Deal("acme-q4", "Acme Q4", "Acme")],
        facts=[what, who],
    )
    claim = PredictedClaim(
        "c", "acme-q4", "what_happened", "Priya Natarajan joined the pricing call"
    )
    cited = "Priya Natarajan discussed pricing on the call"
    assert contradicts(claim, what, day, cited) is False
    assert required_terms_present(claim.text, what, people)


def test_unmatched_deal_cite_on_cluster_is_precision_miss_not_unsupported() -> None:
    day = _day(
        deals=[Deal("a", "A")],
        people=[],
        segments=[_seg("s1", "a", "f1")],
        frames={
            "f1": Frame("f1", "pricing call"),
            "f-ghost": Frame("f-ghost", "pricing call"),
        },
        facts=[GoldFact("g", "a", "what_happened", ("pricing", "call"), {}, ("s1",))],
    )
    gold = DealCorpus(days=[day])
    predicted = PredictedCorpus(
        days=[
            PredictedDay(
                name="day_x",
                segments=[
                    PredictedSegment("s1", ("f1",), NONE),
                    PredictedSegment("ghost-seg", ("f-ghost",), "ghost"),
                ],
                claims=[
                    PredictedClaim(
                        "c1",
                        "ghost",
                        "what_happened",
                        "pricing call",
                        [PredictedEvidence(frame_id="f-missing")],
                    )
                ],
            )
        ]
    )
    score = score_deals(gold, predicted)
    assert score.summary.unsupported == 1
    predicted.days[0].claims[0].evidence = [PredictedEvidence(frame_id="f-ghost")]
    score = score_deals(gold, predicted)
    assert score.summary.unsupported == 0
    assert score.summary.precision == 0.0
    assert score.summary.accuracy == 0.0


def test_privacy_leak_on_bait_frame() -> None:
    bait = Frame("f-bait", "secret privacybait vault", skipped=True, skip_reason="excluded_app")
    day = _day(
        segments=[_seg("s-bait", NONE, "f-bait", bait=True)],
        frames={"f-bait": bait},
        skipped={"f-bait": bait},
    )
    gold = DealCorpus(days=[day])
    leaked = PredictedCorpus(
        days=[
            PredictedDay(
                name="day_x",
                segments=[PredictedSegment("s-bait", ("f-bait",), "deal")],
                claims=[],
            )
        ]
    )
    assert privacy_leak(gold, leaked) is True
    clean = PredictedCorpus(
        days=[
            PredictedDay(
                name="day_x",
                segments=[PredictedSegment("s-bait", ("f-bait",), NONE)],
                claims=[],
            )
        ]
    )
    assert privacy_leak(gold, clean) is False


def test_over_length_claim_cannot_cover() -> None:
    text = " ".join(f"tok{i}" for i in range(CONTENT_TOKEN_CAP + 1))
    fact = GoldFact("f", "d", "what_happened", ("tok0", "tok1"), {}, ("s",))
    claim = PredictedClaim("c", "d", "what_happened", text)
    assert has_phrase(text, "tok0")
    assert len(content_tokens(claim.text)) > CONTENT_TOKEN_CAP
    assert fact.required_terms[0] in normalize(text)


def test_validate_rejects_weekday_date_alias() -> None:
    day = _day(
        deals=[Deal("a", "A")],
        segments=[_seg("s1", "a", "f1")],
        frames={"f1": Frame("f1", "send pricing friday")},
        facts=[
            GoldFact(
                "f",
                "a",
                "next_step",
                ("friday",),
                {"friday": ("14 nov",)},
                ("s1",),
            )
        ],
    )
    with pytest.raises(ValueError, match="weekday"):
        validate_day(day)


def test_one_claim_covers_at_most_one_fact() -> None:
    people = [Person("p", "Ada Lovelace", "Acme")]
    frames = {"f1": Frame("f1", "pricing call q4 deck Ada Lovelace")}
    day = _day(
        deals=[Deal("a", "A", "Acme")],
        people=people,
        segments=[_seg("s1", "a", "f1")],
        frames=frames,
        facts=[
            GoldFact("f-call", "a", "what_happened", ("pricing", "call"), {}, ("s1",)),
            GoldFact("f-deck", "a", "what_happened", ("q4", "deck"), {}, ("s1",)),
        ],
    )
    gold = DealCorpus(days=[day])
    dump = "pricing call q4 deck"
    predicted = PredictedCorpus(
        days=[
            PredictedDay(
                name="day_x",
                segments=[PredictedSegment("s1", ("f1",), "a")],
                claims=[
                    PredictedClaim(
                        "c1",
                        "a",
                        "what_happened",
                        dump,
                        [PredictedEvidence(segment_id="s1")],
                    )
                ],
            )
        ]
    )
    score = score_deals(gold, predicted)
    assert score.summary.slot_tp["what_happened"] == 1
    assert score.summary.what_happened_recall == 0.5

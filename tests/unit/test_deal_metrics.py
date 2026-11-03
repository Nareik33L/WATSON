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
    _claim_near_key,
    _collapse_duplicate_claims,
    _fact_signature,
    align_segments,
    company_cluster,
    content_tokens,
    contradicts,
    dump_windows,
    extract_amounts,
    extract_dates,
    has_phrase,
    is_multi_fact_claim,
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


def test_extra_deal_person_without_evidence_contradicts() -> None:
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
    assert contradicts(claim, what, day, "discussed pricing on the call") is True


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


def test_validate_rejects_exclusive_other_fact_alias() -> None:
    day = _day(
        deals=[Deal("a", "A")],
        segments=[_seg("s1", "a", "f1")],
        frames={"f1": Frame("f1", "pricing call q4 deck")},
        facts=[
            GoldFact("f-call", "a", "what_happened", ("pricing", "call"), {}, ("s1",)),
            GoldFact(
                "f-deck",
                "a",
                "what_happened",
                ("q4", "deck"),
                {"q4": ("pricing",)},
                ("s1",),
            ),
        ],
    )
    with pytest.raises(ValueError, match="exclusive"):
        validate_day(day)


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


def test_validate_rejects_more_than_three_aliases() -> None:
    day = _day(
        deals=[Deal("a", "A")],
        segments=[_seg("s1", "a", "f1")],
        frames={"f1": Frame("f1", "pricing call")},
        facts=[
            GoldFact(
                "f",
                "a",
                "what_happened",
                ("pricing",),
                {"pricing": ("quote", "rate", "ask", "bid")},
                ("s1",),
            )
        ],
    )
    with pytest.raises(ValueError, match="more than 3 aliases"):
        validate_day(day)


def test_validate_rejects_stopword_aliases() -> None:
    day = _day(
        deals=[Deal("a", "A")],
        segments=[_seg("s1", "a", "f1")],
        frames={"f1": Frame("f1", "pricing call")},
        facts=[
            GoldFact(
                "f",
                "a",
                "what_happened",
                ("pricing",),
                {"pricing": ("the of",)},
                ("s1",),
            )
        ],
    )
    with pytest.raises(ValueError, match="stopwords-only"):
        validate_day(day)


def test_validate_rejects_cross_deal_aliases() -> None:
    day = _day(
        deals=[Deal("a", "A"), Deal("b", "B")],
        segments=[_seg("s1", "a", "f1"), _seg("s2", "b", "f2")],
        frames={"f1": Frame("f1", "pricing call"), "f2": Frame("f2", "globex deck")},
        facts=[
            GoldFact("f-a", "a", "what_happened", ("pricing",), {}, ("s1",)),
            GoldFact(
                "f-b",
                "b",
                "what_happened",
                ("deck",),
                {"deck": ("pricing",)},
                ("s2",),
            ),
        ],
    )
    with pytest.raises(ValueError, match="cross-deal"):
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
    dump = "joined the pricing call q4 deck"
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
    assert score.summary.precision == 1.0


def test_extract_amounts_currency_and_comma_groups() -> None:
    assert "amt:90000:usd" in extract_amounts("$90k")
    assert "amt:1000000:usd" in extract_amounts("$1m")
    assert "amt:1200000:usd" in extract_amounts("$1.2M")
    assert extract_amounts("$40,000") == {"amt:40000", "amt:40000:usd"}
    assert "amt:40000:gbp" in extract_amounts("£40k")
    assert extract_amounts("$90k") != extract_amounts("$9k")
    assert extract_amounts("$1m") != extract_amounts("$10m")
    assert extract_amounts("two year term") == {"amt:2"}
    assert extract_amounts("10") == {"amt:10"}
    assert "amt:4" not in extract_amounts("q4")
    assert "amt:40000" in extract_amounts("40k")
    assert "amt:95000:usd" in extract_amounts("USD 95k")
    assert extract_amounts("90,000") == {"amt:90000"}
    assert "amt:1200000" in extract_amounts("1.2m")
    assert "amt:1" not in extract_amounts("1.2m")
    assert {"amt:9000:usd", "amt:12000:usd"} <= extract_amounts("$9–12k")
    assert {"amt:90000", "amt:120000"} <= extract_amounts("90k-120k")
    assert {"amt:90000", "amt:120000"} <= extract_amounts("90k–120k")
    assert "amt:14" not in extract_amounts("14 Nov")
    assert "amt:14" not in extract_amounts("before the 14 Nov demo about $90k")
    assert "amt:90000:usd" in extract_amounts("before the 14 Nov demo about $90k")
    assert "amt:95000:usd" in extract_amounts("USD 95k")
    assert "amt:95000:usd" not in extract_amounts("95k")
    assert "amt:5000000" not in extract_amounts("5m walk")
    assert "amt:4000" not in extract_amounts("4k monitor")
    assert "amt:1" not in extract_amounts("v1.2")
    assert "amt:2" not in extract_amounts("v1.2")
    assert extract_amounts("v1.2") == set()
    assert "amt:40000" in extract_amounts("40k")
    assert "amt:1200000" in extract_amounts("1.2m")
    assert "amt:95000:usd" in extract_amounts("usd 95k")
    assert "amt:40000:gbp" in extract_amounts("GBP 40k")
    assert "amt:12000:eur" in extract_amounts("EUR 12k")
    assert "amt:40000:gbp" not in extract_amounts("40k")
    assert "amt:12000:eur" not in extract_amounts("12k")


def test_collapse_keeps_slot_and_word_order() -> None:
    what = PredictedClaim("c1", "acme-q4", "what_happened", "Priya sent pricing to Acme")
    who = PredictedClaim("c2", "acme-q4", "who_involved", "Priya sent pricing to Acme")
    swapped = PredictedClaim("c3", "acme-q4", "what_happened", "Acme sent pricing to Priya")
    filled = PredictedClaim("c4", "acme-q4", "what_happened", "please Priya sent pricing to Acme")
    other_deal = PredictedClaim("c5", "globex-pilot", "what_happened", "Priya sent pricing to Acme")
    collapsed = _collapse_duplicate_claims([what, who, swapped, filled, other_deal])
    assert [claim.id for claim in collapsed] == ["c1", "c2", "c3", "c5"]
    assert _claim_near_key(what)[1] == "what_happened"
    assert _claim_near_key(who)[1] == "who_involved"
    assert _claim_near_key(what)[2] != _claim_near_key(swapped)[2]


def test_is_multi_fact_two_signatures_and_same_person() -> None:
    priya = Person("priya-natarajan", "Priya Natarajan", "Acme")
    chen = Person("priya-chen", "Priya Chen", "Globex")
    packed_terms = _day(
        people=[priya],
        facts=[
            GoldFact("f-call", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",)),
            GoldFact("f-deck", "acme-q4", "what_happened", ("q4", "deck"), {}, ("s1",)),
        ],
    )
    two = PredictedClaim("c", "acme-q4", "what_happened", "joined the pricing call q4 deck")
    assert is_multi_fact_claim(two, packed_terms) is True
    same_person = _day(
        people=[priya],
        facts=[
            GoldFact(
                "w1",
                "acme-q4",
                "who_involved",
                (),
                {},
                ("s1",),
                person_id="priya-natarajan",
            ),
            GoldFact(
                "w2",
                "acme-q4",
                "who_involved",
                (),
                {},
                ("s2",),
                person_id="priya-natarajan",
            ),
        ],
    )
    who = PredictedClaim("c", "acme-q4", "who_involved", "Priya Natarajan")
    assert is_multi_fact_claim(who, same_person) is False
    assert _fact_signature(same_person.facts[0]) == _fact_signature(same_person.facts[1])
    assert _fact_signature(same_person.facts[0]) == ("person", "priya-natarajan")
    two_people = _day(
        people=[priya, chen],
        facts=[
            GoldFact(
                "w1",
                "acme-q4",
                "who_involved",
                (),
                {},
                ("s1",),
                person_id="priya-natarajan",
            ),
            GoldFact(
                "w2",
                "acme-q4",
                "who_involved",
                (),
                {},
                ("s1",),
                person_id="priya-chen",
            ),
        ],
    )
    both = PredictedClaim("c", "acme-q4", "who_involved", "Priya Natarajan and Priya Chen")
    assert is_multi_fact_claim(both, two_people) is True


def test_next_step_requires_commitment_or_imperative() -> None:
    fact = GoldFact("f", "d", "next_step", ("send", "pricing"), {}, ("s",))
    plain = PredictedClaim("c", "d", "next_step", "pricing by Friday")
    discuss = PredictedClaim("c2", "d", "next_step", "discuss the pricing")
    will = PredictedClaim("c3", "d", "next_step", "will send pricing")
    send = PredictedClaim("c4", "d", "next_step", "send pricing")
    assert slot_modal_ok(plain, fact) is False
    assert slot_modal_ok(discuss, fact) is False
    assert slot_modal_ok(will, fact) is True
    assert slot_modal_ok(send, fact) is True


def test_align_tiebreak_uses_started_at_then_id() -> None:
    day = _day(
        deals=[Deal("a", "A")],
        segments=[_seg("s1", "a", "f1", "f2")],
    )
    late_small_id = PredictedSegment(
        "a-late",
        ("f1", "f2"),
        "late-deal",
        started_at="2026-01-01T10:00:00Z",
    )
    early_large_id = PredictedSegment(
        "z-early",
        ("f1", "f2"),
        "early-deal",
        started_at="2026-01-01T09:00:00Z",
    )
    labels = align_segments(day, [late_small_id, early_large_id])
    assert labels["s1"] == "early-deal"
    high = PredictedSegment("high", ("f1", "f2"), "high-deal", started_at="2026-01-01T11:00:00Z")
    low = PredictedSegment("low", ("f1",), "low-deal", started_at="2026-01-01T08:00:00Z")
    overlap_labels = align_segments(day, [low, high])
    assert overlap_labels["s1"] == "high-deal"


def test_match_clusters_tiebreak_gold_id_then_pred_id() -> None:
    day = _day(
        deals=[Deal("b", "B"), Deal("a", "A")],
        segments=[
            _seg("s1", "a", "f1"),
            _seg("s2", "b", "f2"),
        ],
    )
    mapping = match_clusters(day, {"s1": "p1", "s2": "p1"})
    assert mapping["p1"] == "a"


def test_company_cluster_ignores_deal_name_token() -> None:
    day = _day(
        deals=[Deal("northwind", "Zephyr", "Globex")],
        people=[],
        segments=[_seg("s1", "northwind", "f1", title="Zephyr inbox")],
    )
    assert company_cluster(day.segments[0], day) == NONE
    globex_title = GoldSegment(
        id="s2",
        frame_ids=("f1",),
        deal="northwind",
        bundle_id="com.lumen.mail",
        window_title="Globex inbox",
        started_at="2026-01-01T09:00:00Z",
    )
    assert company_cluster(globex_title, day) == "globex"


def test_contradicts_off_deal_value_in_evidence_still_conflicts() -> None:
    priya = Person("priya-natarajan", "Priya Natarajan", "Acme")
    chen = Person("priya-chen", "Priya Chen", "Globex")
    what = GoldFact("f1", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))
    who = GoldFact(
        "f2",
        "acme-q4",
        "who_involved",
        (),
        {},
        ("s1",),
        person_id="priya-natarajan",
    )
    day = _day(
        people=[priya, chen],
        deals=[Deal("acme-q4", "Acme Q4", "Acme")],
        facts=[what, who],
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "Priya Chen on the pricing call")},
    )
    claim = PredictedClaim(
        "c", "acme-q4", "what_happened", "Priya Chen joined the pricing call"
    )
    assert contradicts(claim, what, day, "Priya Chen on the pricing call") is True
    assert contradicts(claim, what, day, "pricing call") is True

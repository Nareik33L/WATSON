"""Every §11.2 scorer rule, exercised end-to-end through score_deals."""

from __future__ import annotations

from dataclasses import replace

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
    _claim_covered_facts,
    _claim_covers,
    _collapse_duplicate_claims,
    _fact_signature,
    _last_fact_greedy_matching,
    _maximum_claim_fact_matching,
    _pair_claims,
    _stored_pipeline_texts,
    _unsupported,
    align_segments,
    collect_predicted_deals,
    company_cluster,
    content_tokens,
    contradicts,
    deal_term_list,
    extract_amounts,
    fact_terms_satisfied,
    honest_has_roster_name_and_deadline,
    honest_prediction,
    honest_prediction_filled,
    is_multi_fact_claim,
    load_deal_corpus,
    match_clusters,
    matching_slot_facts,
    person_entities_match,
    privacy_bait_legal,
    required_terms_present,
    resolve_person_id,
    score_deals,
    slot_modal_ok,
    validate_day,
)

PRIYA = Person("priya-natarajan", "Priya Natarajan", "Acme")
CHEN = Person("priya-chen", "Priya Chen", "Globex")


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
        deals=deals or [Deal("acme-q4", "Acme Q4", "Acme")],
        people=people or [PRIYA, CHEN],
        segments=segments or [],
        frames=frames or {},
        facts=facts or [],
        skipped=skipped or {},
    )


def _seg(
    seg_id: str,
    deal: str,
    *frames: str,
    title: str = "Re: Q4 — Acme",
    bundle: str = "com.lumen.mail",
    bait: bool = False,
    started: str = "",
    channel: str = "email",
) -> GoldSegment:
    return GoldSegment(
        id=seg_id,
        frame_ids=frames,
        deal=deal,
        bundle_id=bundle,
        window_title=title,
        started_at=started or "2026-01-01T09:00:00Z",
        channel=channel,
        privacy_bait=bait,
    )


def _pred(
    day: DayGold,
    *,
    labels: dict[str, str] | None = None,
    claims: list[PredictedClaim] | None = None,
    stored: list[str] | None = None,
) -> PredictedCorpus:
    labels = labels or {segment.id: segment.deal for segment in day.segments}
    segments = [
        PredictedSegment(
            id=segment.id,
            frame_ids=segment.frame_ids,
            deal=NONE if segment.privacy_bait else labels.get(segment.id, segment.deal),
            started_at=segment.started_at,
        )
        for segment in day.segments
    ]
    return PredictedCorpus(
        days=[
            PredictedDay(
                name=day.name,
                segments=segments,
                claims=claims or [],
                stored_texts=stored or [],
            )
        ]
    )


def _claim(
    text: str,
    *,
    slot: str = "what_happened",
    deal: str = "acme-q4",
    evidence: str = "s1",
    claim_id: str = "c1",
) -> PredictedClaim:
    return PredictedClaim(
        id=claim_id,
        deal=deal,
        slot=slot,
        text=text,
        evidence=[PredictedEvidence(segment_id=evidence)],
    )


def test_score_deals_matches_on_max_overlap_then_pools() -> None:
    day_a = _day(
        name="day_a",
        segments=[
            _seg("s1", "acme-q4", "f1"),
            _seg("s2", "acme-q4", "f2"),
            _seg("s3", NONE, "f3"),
        ],
        frames={"f1": Frame("f1", "a"), "f2": Frame("f2", "b"), "f3": Frame("f3", "n")},
    )
    day_b = _day(
        name="day_b",
        deals=[Deal("globex-pilot", "Globex Pilot", "Globex")],
        people=[CHEN],
        segments=[_seg("s1", "globex-pilot", "g1"), _seg("s2", "globex-pilot", "g2")],
        frames={"g1": Frame("g1", "x"), "g2": Frame("g2", "y")},
    )
    gold = DealCorpus(days=[day_a, day_b])
    predicted = PredictedCorpus(
        days=[
            PredictedDay(
                name="day_a",
                segments=[
                    PredictedSegment("p1", ("f1", "f2"), "cluster-a"),
                    PredictedSegment("p2", ("f3",), NONE),
                ],
                claims=[],
            ),
            PredictedDay(
                name="day_b",
                segments=[
                    PredictedSegment("q1", ("g1",), "cluster-b"),
                    PredictedSegment("q2", ("g2",), "cluster-b"),
                ],
                claims=[],
            ),
        ]
    )
    score = score_deals(gold, predicted)
    assert score.grouping.assignment_accuracy == 1.0
    assert score.grouping.deal_segment_recall == 1.0
    assert score.grouping.pairwise_f1 == 1.0
    assert score.grouping.pairwise_tp == 2


def test_score_deals_none_is_its_own_class() -> None:
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1"), _seg("s2", NONE, "f2")],
        frames={"f1": Frame("f1", "deal"), "f2": Frame("f2", "noise")},
    )
    score = score_deals(DealCorpus(days=[day]), _pred(day, labels={"s1": NONE, "s2": NONE}))
    assert score.grouping.deal_segment_recall == 0.0
    assert score.grouping.assignment_accuracy == 0.5


def test_score_deals_aliases_match_required_terms() -> None:
    fact = GoldFact(
        "g",
        "acme-q4",
        "what_happened",
        ("pricing", "call"),
        {"pricing": ("price",)},
        ("s1",),
    )
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "price call with Priya Natarajan")},
        facts=[fact],
    )
    predicted = _pred(day, claims=[_claim("joined the price call")])
    score = score_deals(DealCorpus(days=[day]), predicted)
    assert score.summary.correct == 1
    assert score.summary.what_happened_recall == 1.0
    assert score.summary.accuracy == 1.0


def test_score_deals_date_swap_is_a_wrong_claim() -> None:
    fact = GoldFact(
        "g",
        "acme-q4",
        "what_happened",
        ("pricing", "call"),
        {"date": ("14 nov",)},
        ("s1",),
    )
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call 14 Nov Priya Natarajan")},
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    honest = _pred(day, claims=[_claim("joined the pricing call before 14 Nov")])
    swapped = _pred(day, claims=[_claim("joined the pricing call before 15 Nov")])
    assert score_deals(gold, honest).summary.correct == 1
    score = score_deals(gold, swapped)
    assert score.summary.correct == 0
    assert score.summary.what_happened_recall == 0.0
    assert score.summary.accuracy == 0.0


def test_score_deals_money_swap_90k_vs_9k() -> None:
    fact = GoldFact(
        "g",
        "acme-q4",
        "what_happened",
        ("pricing", "call"),
        {"amount": ("$90k",)},
        ("s1",),
    )
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call $90k Priya Natarajan")},
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    honest = _pred(day, claims=[_claim("joined the pricing call at $90k")])
    swapped = _pred(day, claims=[_claim("joined the pricing call at $9k")])
    assert "amt:90000:usd" in extract_amounts("$90k")
    assert "amt:9000:usd" in extract_amounts("$9k")
    assert score_deals(gold, honest).summary.correct == 1
    score = score_deals(gold, swapped)
    assert score.summary.correct == 0
    assert score.summary.accuracy == 0.0


def test_score_deals_money_swap_1m_vs_10m() -> None:
    fact = GoldFact(
        "g",
        "acme-q4",
        "what_happened",
        ("pricing", "call"),
        {"amount": ("$1m",)},
        ("s1",),
    )
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call $1m Priya Natarajan")},
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    honest = _pred(day, claims=[_claim("joined the pricing call at $1m")])
    swapped = _pred(day, claims=[_claim("joined the pricing call at $10m")])
    assert "amt:1000000:usd" in extract_amounts("$1m")
    assert "amt:10000000:usd" in extract_amounts("$10m")
    assert score_deals(gold, honest).summary.correct == 1
    score = score_deals(gold, swapped)
    assert score.summary.correct == 0
    assert score.summary.accuracy == 0.0


def test_score_deals_amount_swap_is_a_wrong_claim() -> None:
    fact = GoldFact(
        "g",
        "acme-q4",
        "what_happened",
        ("two", "year", "term"),
        {"two": ("2",)},
        ("s1",),
    )
    day = _day(
        deals=[Deal("acme-q4", "Acme Q4", "Acme")],
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "two year term Priya Natarajan")},
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    honest = _pred(day, claims=[_claim("agreed a two year term")])
    swapped = _pred(day, claims=[_claim("agreed a three year term")])
    assert score_deals(gold, honest).summary.correct == 1
    score = score_deals(gold, swapped)
    assert score.summary.correct == 0
    assert score.summary.accuracy == 0.0


def test_score_deals_person_swap_is_a_wrong_claim() -> None:
    fact = GoldFact(
        "g",
        "acme-q4",
        "who_involved",
        (),
        {},
        ("s1",),
        person_id="priya-natarajan",
    )
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "Priya Natarajan emailed")},
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    honest = _pred(day, claims=[_claim("Priya Natarajan", slot="who_involved")])
    swapped = _pred(day, claims=[_claim("Priya Chen", slot="who_involved")])
    assert score_deals(gold, honest).summary.correct == 1
    score = score_deals(gold, swapped)
    assert score.summary.correct == 0
    assert score.summary.who_involved_recall == 0.0


def test_score_deals_corpus_date_amount_person_swaps() -> None:
    gold = load_deal_corpus()
    honest = honest_prediction(gold)
    base = score_deals(gold, honest)
    assert base.summary.accuracy >= 0.85

    def _swap(needle: str, replacement: str) -> PredictedCorpus:
        days = []
        for day in honest.days:
            claims = [
                replace(claim, text=claim.text.replace(needle, replacement, 1))
                for claim in day.claims
            ]
            days.append(replace(day, claims=claims))
        return PredictedCorpus(days=days)

    date_score = score_deals(gold, _swap("14 Nov", "15 Nov"))
    assert date_score.summary.correct < base.summary.correct
    assert date_score.summary.what_happened_recall < 1.0
    amount_score = score_deals(gold, _swap("two year", "three year"))
    assert amount_score.summary.correct < base.summary.correct
    money_90k = score_deals(gold, _swap("$90k", "$9k"))
    assert money_90k.summary.correct < base.summary.correct
    money_1m = score_deals(gold, _swap("$1m", "$10m"))
    assert money_1m.summary.correct < base.summary.correct
    person_score = score_deals(gold, _swap("Priya Natarajan", "Priya Chen"))
    assert person_score.summary.correct < base.summary.correct


def test_score_deals_evidence_overlap_required() -> None:
    fact = GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1"), _seg("s2", "acme-q4", "f2")],
        frames={
            "f1": Frame("f1", "pricing call"),
            "f2": Frame("f2", "pricing call"),
        },
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    miss = _pred(day, claims=[_claim("joined the pricing call", evidence="s2")])
    hit = _pred(day, claims=[_claim("joined the pricing call", evidence="s1")])
    assert score_deals(gold, miss).summary.correct == 0
    assert score_deals(gold, miss).summary.unsupported == 0
    assert score_deals(gold, hit).summary.correct == 1
    assert score_deals(gold, hit).summary.unsupported == 0
    miss_day = miss.days[0]
    assert _unsupported(day, miss_day, {"acme-q4": "acme-q4"}, miss_day.claims[0], fact) is True


def test_score_deals_citation_only_unsupported() -> None:
    fact = GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call"), "f-ghost": Frame("f-ghost", "pricing call")},
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    missing_cite = _pred(
        day,
        claims=[
            PredictedClaim(
                "c1",
                "acme-q4",
                "what_happened",
                "joined the pricing call",
                [PredictedEvidence(frame_id="f-missing")],
            )
        ],
    )
    assert score_deals(gold, missing_cite).summary.unsupported == 1
    ghost = PredictedCorpus(
        days=[
            PredictedDay(
                name="day_x",
                segments=[
                    PredictedSegment("s1", ("f1",), NONE),
                    PredictedSegment("ghost", ("f-ghost",), "ghost"),
                ],
                claims=[
                    PredictedClaim(
                        "c1",
                        "ghost",
                        "what_happened",
                        "joined the pricing call",
                        [PredictedEvidence(frame_id="f-ghost")],
                    )
                ],
            )
        ]
    )
    score = score_deals(gold, ghost)
    assert score.summary.unsupported == 0
    assert score.summary.precision == 0.0


def test_score_deals_content_token_cap() -> None:
    fact = GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call")},
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    pad = " ".join(f"tok{i}" for i in range(CONTENT_TOKEN_CAP))
    over = _pred(day, claims=[_claim(f"{pad} pricing call")])
    under = _pred(day, claims=[_claim("joined the pricing call")])
    assert score_deals(gold, over).summary.correct == 0
    assert score_deals(gold, under).summary.correct == 1


def test_score_deals_one_claim_covers_at_most_one_fact() -> None:
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call q4 deck 14 Nov demo")},
        facts=[
            GoldFact(
                "f-call",
                "acme-q4",
                "what_happened",
                ("pricing", "call"),
                {"date": ("14 nov",)},
                ("s1",),
            ),
            GoldFact("f-deck", "acme-q4", "what_happened", ("q4", "deck"), {}, ("s1",)),
            GoldFact("f-demo", "acme-q4", "what_happened", ("demo",), {}, ("s1",)),
        ],
    )
    gold = DealCorpus(days=[day])
    dump = _pred(day, claims=[_claim("joined the pricing call q4 deck")])
    score = score_deals(gold, dump)
    assert score.summary.correct == 1
    assert score.summary.claims == 1
    assert score.summary.what_happened_recall == pytest.approx(1 / 3)
    assert score.summary.precision == 1.0
    for text in (
        "joined the pricing call before the 14 Nov demo",
        "Edited the Q4 deck after the pricing call",
    ):
        natural = score_deals(gold, _pred(day, claims=[_claim(text)]))
        assert natural.summary.correct == 1, text
        assert natural.summary.what_happened_recall == pytest.approx(1 / 3), text
    twins = _pred(
        day,
        claims=[
            _claim("joined the pricing call q4 deck", claim_id="c1"),
            _claim("joined the pricing call q4 deck", claim_id="c2"),
        ],
    )
    twin_score = score_deals(gold, twins)
    assert twin_score.summary.correct == 1
    assert twin_score.summary.claims == 2
    assert twin_score.summary.what_happened_recall == pytest.approx(1 / 3)
    assert twin_score.summary.precision == pytest.approx(0.5)
    near = _pred(
        day,
        claims=[
            _claim("joined the pricing call q4 deck", claim_id="n1"),
            _claim("joined the pricing call q4 deck please", claim_id="n2"),
            _claim("still joined the pricing call q4 deck", claim_id="n3"),
        ],
    )
    near_score = score_deals(gold, near)
    assert near_score.summary.correct == 1
    assert near_score.summary.claims == 3
    assert near_score.summary.what_happened_recall == pytest.approx(1 / 3)


def test_score_deals_natural_paraphrase_keeps_date_and_extra_term() -> None:
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call 14 Nov demo $90k")},
        facts=[
            GoldFact(
                "f-call",
                "acme-q4",
                "what_happened",
                ("pricing", "call"),
                {"date": ("14 nov",), "amount": ("$90k",)},
                ("s1",),
            ),
            GoldFact("f-demo", "acme-q4", "what_happened", ("demo",), {}, ("s1",)),
        ],
    )
    gold = DealCorpus(days=[day])
    natural = _pred(
        day,
        claims=[_claim("joined the pricing call before the 14 Nov demo about $90k")],
    )
    score = score_deals(gold, natural)
    assert score.summary.correct == 1
    assert score.summary.what_happened_recall == 0.5
    assert "amt:14" not in extract_amounts("before the 14 Nov demo about $90k")


def test_score_deals_what_modal_future_phrases_rejected() -> None:
    fact = GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call")},
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    for text in (
        "will join the pricing call",
        "going to join the pricing call",
        "need to join the pricing call",
        "plan to join the pricing call",
    ):
        assert score_deals(gold, _pred(day, claims=[_claim(text)])).summary.correct == 0, text


def test_score_deals_ill_is_read_as_will() -> None:
    what = GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))
    nxt = GoldFact("n", "acme-q4", "next_step", ("send", "pricing"), {}, ("s1",))
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call send pricing")},
        facts=[what, nxt],
    )
    gold = DealCorpus(days=[day])
    past = _pred(day, claims=[_claim("I'll join the pricing call")])
    commit = _pred(day, claims=[_claim("I'll send pricing", slot="next_step")])
    assert score_deals(gold, past).summary.correct == 0
    assert score_deals(gold, commit).summary.next_step_recall == 1.0
    assert slot_modal_ok(_claim("I'll join the pricing call"), what) is False
    assert slot_modal_ok(_claim("I'll send pricing", slot="next_step"), nxt) is True


def test_score_deals_what_modal_will_rejected() -> None:
    fact = GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call")},
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    score = score_deals(gold, _pred(day, claims=[_claim("will join the pricing call")]))
    assert score.summary.correct == 0


def test_score_deals_next_step_may_and_sent_rejected() -> None:
    fact = GoldFact("g", "acme-q4", "next_step", ("send", "pricing"), {}, ("s1",))
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "send pricing Friday")},
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    may = _pred(day, claims=[_claim("may send pricing", slot="next_step")])
    sent = _pred(day, claims=[_claim("sent pricing", slot="next_step")])
    will = _pred(day, claims=[_claim("will send revised pricing", slot="next_step")])
    assert score_deals(gold, may).summary.correct == 0
    assert score_deals(gold, sent).summary.correct == 0
    assert score_deals(gold, will).summary.correct == 1
    might = _pred(day, claims=[_claim("might send pricing", slot="next_step")])
    could = _pred(day, claims=[_claim("could send pricing", slot="next_step")])
    assert score_deals(gold, might).summary.correct == 0
    assert score_deals(gold, could).summary.correct == 0
    hedge_tail = _pred(day, claims=[_claim("send pricing might", slot="next_step")])
    assert score_deals(gold, hedge_tail).summary.correct == 0
    assert slot_modal_ok(_claim("send pricing might", slot="next_step"), fact) is False


def test_score_deals_privacy_fail_zeros_accuracy() -> None:
    bait = Frame("f-bait", "secret privacybait vault", skipped=True, skip_reason="excluded_app")
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1"), _seg("s-bait", NONE, "f-bait", bait=True)],
        frames={"f1": Frame("f1", "pricing call"), "f-bait": bait},
        skipped={"f-bait": bait},
        facts=[GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))],
    )
    gold = DealCorpus(days=[day])
    leaked = _pred(
        day,
        labels={"s1": "acme-q4", "s-bait": "acme-q4"},
        claims=[_claim("joined the pricing call")],
        stored=["leaked privacybait line"],
    )
    score = score_deals(gold, leaked)
    assert score.privacy_fail is True
    assert score.grouping.accuracy == 0.0
    assert score.summary.accuracy == 0.0


def test_score_deals_missing_required_term_does_not_cover() -> None:
    fact = GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call")},
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    miss = _pred(day, claims=[_claim("joined the call")])
    hit = _pred(day, claims=[_claim("joined the pricing call")])
    assert score_deals(gold, miss).summary.correct == 0
    assert score_deals(gold, hit).summary.correct == 1


def test_score_deals_unmatched_cite_off_cluster_is_unsupported() -> None:
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call"), "f-ghost": Frame("f-ghost", "pricing call")},
        facts=[GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))],
    )
    predicted = PredictedCorpus(
        days=[
            PredictedDay(
                name="day_x",
                segments=[
                    PredictedSegment("s1", ("f1",), NONE),
                    PredictedSegment("ghost", ("f-ghost",), "ghost"),
                ],
                claims=[
                    PredictedClaim(
                        "c1",
                        "ghost",
                        "what_happened",
                        "joined the pricing call",
                        [PredictedEvidence(frame_id="f1")],
                    )
                ],
            )
        ]
    )
    score = score_deals(DealCorpus(days=[day]), predicted)
    assert score.summary.unsupported == 1
    assert score.summary.accuracy == 0.0


def test_who_shared_roster_name_needs_company() -> None:
    acme = Person("priya-acme", "Priya Natarajan", "Acme")
    globex = Person("priya-globex", "Priya Natarajan", "Globex")
    people = [acme, globex]
    fact = GoldFact(
        "g",
        "acme-q4",
        "who_involved",
        (),
        {},
        ("s1",),
        person_id="priya-acme",
    )
    assert person_entities_match("Priya Natarajan", "priya-acme", people) is False
    assert person_entities_match("Priya Natarajan Acme", "priya-acme", people) is True
    assert fact_terms_satisfied("Priya Natarajan", fact, people) is False
    assert fact_terms_satisfied("Priya Natarajan Acme", fact, people) is True


def test_honest_roster_name_and_deadline_must_be_what_happened() -> None:
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "Priya Natarajan Friday pricing")},
        people=[PRIYA],
    )
    gold = DealCorpus(days=[day])
    nxt = _pred(
        day,
        claims=[_claim("Priya Natarajan will send pricing Friday", slot="next_step")],
    )
    what = _pred(
        day,
        claims=[_claim("Priya Natarajan sent pricing Friday")],
    )
    assert honest_has_roster_name_and_deadline(gold, nxt) is False
    assert honest_has_roster_name_and_deadline(gold, what) is True


def test_score_deals_honest_control_on_gold() -> None:
    gold = load_deal_corpus()
    bare = score_deals(gold, honest_prediction(gold))
    filled = score_deals(gold, honest_prediction_filled(gold))
    for score in (bare, filled):
        assert score.grouping.accuracy == 1.0
        assert score.summary.accuracy == pytest.approx(1.0)
        assert score.summary.unsupported == 0
        assert not score.privacy_fail


def test_honest_paraphrases_that_name_an_earlier_fact_score_1() -> None:
    gold = load_deal_corpus()
    predicted = honest_prediction(gold)
    rewritten = 0
    for day in predicted.days:
        for claim in day.claims:
            if claim.text == "Noted the 14 Nov demo":
                claim.text = "Noted the 14 Nov demo after the pricing call"
                rewritten += 1
            if claim.text == "Opened the onboarding draft":
                claim.text = "Opened the onboarding draft after editing the onboarding page"
                rewritten += 1
    assert rewritten == 2
    score = score_deals(gold, predicted)
    assert score.summary.accuracy == pytest.approx(1.0)
    assert score.summary.what_happened_recall == pytest.approx(1.0)
    assert score.summary.unsupported == 0


def test_max_matching_kills_last_covered_fact_greedy() -> None:
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call 14 Nov demo")},
        facts=[
            GoldFact(
                "f-call",
                "acme-q4",
                "what_happened",
                ("pricing", "call"),
                {"date": ("14 nov",)},
                ("s1",),
            ),
            GoldFact(
                "f-demo", "acme-q4", "what_happened", ("demo",), {"date": ("14 nov",)}, ("s1",)
            ),
        ],
    )
    both = _claim("Noted the 14 Nov demo after the pricing call", claim_id="c-both")
    late = _claim("Noted the 14 Nov demo", claim_id="c-late")
    early = _claim("joined the pricing call", claim_id="c-early")
    gold = DealCorpus(days=[day])
    pred = _pred(day, claims=[both, late, early]).days[0]
    mapping = {"acme-q4": "acme-q4"}
    cover_of = {claim.id: _claim_covered_facts(day, pred, mapping, claim) for claim in pred.claims}
    assert {fact.id for fact in cover_of["c-both"]} == {"f-call", "f-demo"}
    assert {fact.id for fact in cover_of["c-late"]} == {"f-demo"}
    assert {fact.id for fact in cover_of["c-early"]} == {"f-call"}
    assert len(_maximum_claim_fact_matching([both, late], cover_of)) == 2
    assert len(_last_fact_greedy_matching([both, late], cover_of)) == 1
    assert len(_maximum_claim_fact_matching([both, early], cover_of)) == 2
    assert len(_last_fact_greedy_matching([both, early], cover_of)) == 2
    late_score = score_deals(gold, _pred(day, claims=[both, late]))
    assert late_score.summary.correct == 2
    assert late_score.summary.what_happened_recall == pytest.approx(1.0)
    early_score = score_deals(gold, _pred(day, claims=[both, early]))
    assert early_score.summary.correct == 2
    assert early_score.summary.what_happened_recall == pytest.approx(1.0)


def test_leftover_collapse_requires_slot_deal_and_citation_overlap() -> None:
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1"), _seg("s2", "acme-q4", "f2")],
        frames={
            "f1": Frame("f1", "pricing call demo"),
            "f2": Frame("f2", "pricing call demo"),
        },
        facts=[
            GoldFact("f-call", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",)),
            GoldFact("f-demo", "acme-q4", "what_happened", ("demo",), {}, ("s1",)),
        ],
    )
    matched = _claim("Noted the 14 Nov demo after the pricing call", claim_id="kept")
    copy = _claim("Noted the 14 Nov demo after the pricing call", claim_id="copy")
    other_slot = _claim(
        "Noted the 14 Nov demo after the pricing call",
        slot="next_step",
        claim_id="next",
    )
    other_deal = _claim(
        "Noted the 14 Nov demo after the pricing call",
        deal="other",
        claim_id="deal",
    )
    disjoint = PredictedClaim(
        "disjoint",
        "acme-q4",
        "what_happened",
        "Noted the 14 Nov demo after the pricing call",
        [PredictedEvidence(segment_id="s2")],
    )
    pred = _pred(day, claims=[matched, copy, other_slot, other_deal, disjoint]).days[0]
    cover = frozenset({"f-call", "f-demo"})
    subset = frozenset({"f-demo"})
    collapsed = _collapse_duplicate_claims(
        pred.claims,
        day,
        pred,
        {"acme-q4": "acme-q4", "other": "acme-q4"},
        matched=[matched],
        cover_of={
            "kept": cover,
            "copy": subset,
            "next": subset,
            "deal": subset,
            "disjoint": subset,
        },
        cited_of={
            "kept": frozenset({"s1"}),
            "copy": frozenset({"s1"}),
            "next": frozenset({"s1"}),
            "deal": frozenset({"s1"}),
            "disjoint": frozenset({"s2"}),
        },
    )
    ids = {claim.id for claim in collapsed}
    assert ids == {"kept", "next", "deal", "disjoint"}


def test_collapse_requires_citation_overlap() -> None:
    day = _day(
        segments=[
            _seg("s1", "acme-q4", "f1"),
            _seg("s2", "acme-q4", "f2"),
            _seg("s3", "acme-q4", "f3"),
        ],
        frames={
            "f1": Frame("f1", "pricing call"),
            "f2": Frame("f2", "q4 deck"),
            "f3": Frame("f3", "pricing call q4 deck"),
        },
        facts=[
            GoldFact("f-call", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1", "s3")),
            GoldFact("f-deck", "acme-q4", "what_happened", ("q4", "deck"), {}, ("s2", "s3")),
        ],
    )
    gold = DealCorpus(days=[day])
    split = _pred(
        day,
        claims=[
            PredictedClaim(
                "c-ab",
                "acme-q4",
                "what_happened",
                "joined the pricing call q4 deck",
                [PredictedEvidence(segment_id="s1"), PredictedEvidence(segment_id="s2")],
            ),
            PredictedClaim(
                "c-c",
                "acme-q4",
                "what_happened",
                "joined the pricing call q4 deck",
                [PredictedEvidence(segment_id="s3")],
            ),
        ],
    )
    score = score_deals(gold, split)
    assert score.summary.correct == 2
    assert score.summary.claims == 2
    assert score.summary.what_happened_recall == pytest.approx(1.0)


def test_score_deals_uncovered_gold_is_missing() -> None:
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "x")},
    )
    predicted = PredictedCorpus(days=[PredictedDay(name="day_x", segments=[], claims=[])])
    score = score_deals(DealCorpus(days=[day]), predicted)
    assert score.grouping.assignment_accuracy == 0.0
    assert score.grouping.deal_segment_recall == 0.0
    _ = MISSING


def test_extract_amounts_currency_forms() -> None:
    assert "amt:90000:usd" in extract_amounts("$90k")
    assert "amt:1000000:usd" in extract_amounts("$1m")
    assert "amt:1200000:usd" in extract_amounts("$1.2M")
    assert extract_amounts("$40,000") == {"amt:40000", "amt:40000:usd"}
    assert "amt:40000:gbp" in extract_amounts("£40k")
    assert "amt:40" not in extract_amounts("$40,000")
    assert "amt:0" not in extract_amounts("$40,000")
    assert extract_amounts("$90k") != extract_amounts("$9k")
    assert extract_amounts("$1m") != extract_amounts("$10m")
    assert {"amt:90000:usd", "amt:40000:gbp"} <= extract_amounts("Send $90k and £40k")
    assert extract_amounts("10") == {"amt:10"}
    assert extract_amounts("40") == {"amt:40"}
    assert "amt:4" not in extract_amounts("q4")
    assert "amt:40000" in extract_amounts("40k")
    assert "amt:95000" in extract_amounts("95k")
    assert "amt:95000:usd" in extract_amounts("USD 95k")
    assert "amt:95000:usd" not in extract_amounts("95k")
    assert "amt:5000000" not in extract_amounts("5m walk")
    assert "amt:4000" not in extract_amounts("4k monitor")
    assert extract_amounts("v1.2") == set()
    assert extract_amounts("90,000") == {"amt:90000"}
    assert "amt:90" not in extract_amounts("90,000")
    assert "amt:0" not in extract_amounts("90,000")
    assert "amt:1200000" in extract_amounts("1.2m")
    assert "amt:1" not in extract_amounts("1.2m")
    range_low = extract_amounts("$9–12k")
    assert "amt:9000:usd" in range_low
    assert "amt:12000:usd" in range_low
    range_both = extract_amounts("$9k–12k")
    assert "amt:9000:usd" in range_both
    assert "amt:12000:usd" in range_both
    range_bare = extract_amounts("90k-120k")
    assert "amt:90000" in range_bare
    assert "amt:120000" in range_bare
    range_en = extract_amounts("90k–120k")
    assert "amt:90000" in range_en
    assert "amt:120000" in range_en
    dated = extract_amounts("before the 14 Nov demo about $90k")
    assert "amt:90000:usd" in dated
    assert "amt:14" not in dated
    assert "amt:14" not in extract_amounts("14 Nov")
    assert "amt:14" not in extract_amounts("2026-11-14")


def test_score_deals_ocr_missing_terms_zeros_accuracy() -> None:
    fact = GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "hello world no terms here")},
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    score = score_deals(gold, _pred(day, claims=[_claim("joined the pricing call")]))
    assert score.summary.correct == 1
    assert score.summary.unsupported == 1
    assert score.summary.accuracy == 0.0


def test_score_deals_grouping_accuracy_is_min() -> None:
    day = _day(
        deals=[Deal("acme-q4", "Acme Q4", "Acme"), Deal("globex-pilot", "Globex", "Globex")],
        segments=[
            _seg("s1", "acme-q4", "f1"),
            _seg("s2", "acme-q4", "f2"),
            _seg("s3", "globex-pilot", "f3"),
            _seg("s4", "globex-pilot", "f4"),
            _seg("s5", NONE, "f5"),
            _seg("s6", NONE, "f6"),
            _seg("s7", NONE, "f7"),
            _seg("s8", NONE, "f8"),
        ],
        frames={
            "f1": Frame("f1", "a"),
            "f2": Frame("f2", "b"),
            "f3": Frame("f3", "c"),
            "f4": Frame("f4", "d"),
            "f5": Frame("f5", "n"),
            "f6": Frame("f6", "n"),
            "f7": Frame("f7", "n"),
            "f8": Frame("f8", "n"),
        },
    )
    predicted = _pred(
        day,
        labels={
            "s1": "acme-q4",
            "s2": "acme-q4",
            "s3": NONE,
            "s4": NONE,
            "s5": NONE,
            "s6": NONE,
            "s7": NONE,
            "s8": NONE,
        },
    )
    score = score_deals(DealCorpus(days=[day]), predicted)
    expected = min(
        score.grouping.assignment_accuracy,
        score.grouping.deal_segment_recall,
        score.grouping.pairwise_f1,
    )
    assert score.grouping.deal_segment_recall < score.grouping.assignment_accuracy
    assert score.grouping.accuracy == expected
    assert score.grouping.accuracy == score.grouping.deal_segment_recall


def test_score_deals_grouping_min_drops_assignment() -> None:
    day = _day(
        segments=[
            _seg("s1", "acme-q4", "f1"),
            _seg("s2", "acme-q4", "f2"),
            _seg("s3", NONE, "f3"),
            _seg("s4", NONE, "f4"),
            _seg("s5", NONE, "f5"),
            _seg("s6", NONE, "f6"),
        ],
        frames={
            "f1": Frame("f1", "a"),
            "f2": Frame("f2", "b"),
            "f3": Frame("f3", "n"),
            "f4": Frame("f4", "n"),
            "f5": Frame("f5", "n"),
            "f6": Frame("f6", "n"),
        },
    )
    predicted = _pred(
        day,
        labels={
            "s1": "acme-q4",
            "s2": "acme-q4",
            "s3": "noise-3",
            "s4": "noise-4",
            "s5": "noise-5",
            "s6": "noise-6",
        },
    )
    score = score_deals(DealCorpus(days=[day]), predicted)
    assert score.grouping.assignment_accuracy < score.grouping.deal_segment_recall
    assert score.grouping.assignment_accuracy < score.grouping.pairwise_f1
    assert score.grouping.accuracy == score.grouping.assignment_accuracy


def test_score_deals_summary_accuracy_is_min_of_precision_and_recalls() -> None:
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call send pricing Priya Natarajan")},
        facts=[
            GoldFact("f-call", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",)),
            GoldFact("f-deck", "acme-q4", "what_happened", ("q4", "deck"), {}, ("s1",)),
            GoldFact("f-next", "acme-q4", "next_step", ("send", "pricing"), {}, ("s1",)),
            GoldFact(
                "f-who",
                "acme-q4",
                "who_involved",
                (),
                {},
                ("s1",),
                person_id="priya-natarajan",
            ),
        ],
    )
    predicted = _pred(
        day,
        claims=[
            _claim("joined the pricing call", claim_id="c-what"),
            _claim("will send pricing", slot="next_step", claim_id="c-next"),
            _claim("Priya Natarajan", slot="who_involved", claim_id="c-who"),
        ],
    )
    score = score_deals(DealCorpus(days=[day]), predicted)
    assert score.summary.precision == 1.0
    assert score.summary.what_happened_recall == 0.5
    assert score.summary.next_step_recall == 1.0
    assert score.summary.who_involved_recall == 1.0
    assert score.summary.unsupported == 0
    assert score.summary.accuracy == 0.5


def test_score_deals_privacy_claim_evidence_frame() -> None:
    bait = Frame("f-bait", "secret privacybait vault", skipped=True, skip_reason="excluded_app")
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1"), _seg("s-bait", NONE, "f-bait", bait=True)],
        frames={"f1": Frame("f1", "pricing call"), "f-bait": bait},
        skipped={"f-bait": bait},
    )
    leaked = _pred(
        day,
        claims=[
            PredictedClaim(
                "c1",
                "acme-q4",
                "what_happened",
                "joined the pricing call",
                [PredictedEvidence(frame_id="f-bait")],
            )
        ],
    )
    score = score_deals(DealCorpus(days=[day]), leaked)
    assert score.privacy_fail is True
    assert score.grouping.accuracy == 0.0
    assert score.summary.accuracy == 0.0


def test_score_deals_privacy_claim_evidence_segment() -> None:
    bait = Frame("f-bait", "secret privacybait vault", skipped=True, skip_reason="excluded_app")
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1"), _seg("s-bait", NONE, "f-bait", bait=True)],
        frames={"f1": Frame("f1", "pricing call"), "f-bait": bait},
        skipped={"f-bait": bait},
    )
    leaked = _pred(
        day,
        claims=[
            PredictedClaim(
                "c1",
                "acme-q4",
                "what_happened",
                "joined the pricing call",
                [PredictedEvidence(segment_id="s-bait")],
            )
        ],
    )
    score = score_deals(DealCorpus(days=[day]), leaked)
    assert score.privacy_fail is True


def test_score_deals_privacy_stored_texts() -> None:
    bait = Frame("f-bait", "secret privacybait vault", skipped=True, skip_reason="excluded_app")
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1"), _seg("s-bait", NONE, "f-bait", bait=True)],
        frames={"f1": Frame("f1", "pricing call"), "f-bait": bait},
        skipped={"f-bait": bait},
    )
    leaked = _pred(day, stored=["leaked privacybait line"])
    score = score_deals(DealCorpus(days=[day]), leaked)
    assert score.privacy_fail is True
    by_id = _pred(day, stored=[f"stored frame {bait.id}"])
    assert score_deals(DealCorpus(days=[day]), by_id).privacy_fail is True


def test_resolve_person_id_ignores_non_roster_capitals() -> None:
    people = [PRIYA, CHEN]
    assert resolve_person_id("Opened the Hours Grid today", people) is None
    assert resolve_person_id("Jordan published the deck", people) is None
    assert resolve_person_id("Please Send Pricing", people) is None
    assert resolve_person_id("Priya Natarajan emailed", people) == "priya-natarajan"


def test_score_deals_who_rejects_any_capitalised_word() -> None:
    fact = GoldFact(
        "g",
        "acme-q4",
        "who_involved",
        (),
        {},
        ("s1",),
        person_id="priya-natarajan",
    )
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "Priya Natarajan emailed")},
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    for text in ("Hours Grid", "Jordan Published", "Opened Draft"):
        score = score_deals(gold, _pred(day, claims=[_claim(text, slot="who_involved")]))
        assert score.summary.correct == 0, text
    hit = score_deals(gold, _pred(day, claims=[_claim("Priya Natarajan", slot="who_involved")]))
    assert hit.summary.correct == 1


def test_score_deals_invented_bare_amount_is_a_wrong_claim() -> None:
    fact = GoldFact(
        "g",
        "acme-q4",
        "what_happened",
        ("pricing", "call"),
        {"amount": ("$90k",)},
        ("s1",),
    )
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call $90k Priya Natarajan")},
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    honest = _pred(day, claims=[_claim("joined the pricing call at $90k")])
    assert score_deals(gold, honest).summary.correct == 1
    for text in (
        "joined the pricing call about 95k",
        "joined the pricing call at USD 95k",
        "joined the pricing call at 40k",
    ):
        score = score_deals(gold, _pred(day, claims=[_claim(text)]))
        assert score.summary.correct == 0, text


def test_score_deals_vacuous_pairwise_without_multi_segment_deal() -> None:
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1"), _seg("s2", NONE, "f2")],
        frames={"f1": Frame("f1", "a"), "f2": Frame("f2", "n")},
    )
    score = score_deals(DealCorpus(days=[day]), _pred(day))
    assert score.grouping.pairwise_tp == 0
    assert score.grouping.pairwise_fp == 0
    assert score.grouping.pairwise_fn == 0
    assert score.grouping.pairwise_precision == 1.0
    assert score.grouping.pairwise_recall == 1.0
    assert score.grouping.pairwise_f1 == 1.0
    assert score.grouping.accuracy == 1.0


def test_score_deals_empty_claims_precision_depends_on_gold_facts() -> None:
    with_facts = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call")},
        facts=[GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))],
    )
    empty = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "x")},
    )
    scored_facts = score_deals(DealCorpus(days=[with_facts]), _pred(with_facts))
    scored_empty = score_deals(DealCorpus(days=[empty]), _pred(empty))
    assert scored_facts.summary.claims == 0
    assert scored_facts.summary.precision == 0.0
    assert scored_empty.summary.claims == 0
    assert scored_empty.summary.precision == 1.0


def test_score_deals_missing_slot_recall_defaults_to_one() -> None:
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "send pricing Friday")},
        facts=[GoldFact("g", "acme-q4", "next_step", ("send", "pricing"), {}, ("s1",))],
    )
    predicted = _pred(day, claims=[_claim("will send pricing", slot="next_step")])
    score = score_deals(DealCorpus(days=[day]), predicted)
    assert score.summary.next_step_recall == 1.0
    assert score.summary.what_happened_recall == 1.0
    assert score.summary.who_involved_recall == 1.0
    assert score.summary.accuracy == 1.0


def test_score_deals_exactly_cap_tokens_still_covers() -> None:
    fact = GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call")},
        facts=[fact],
    )
    pad = " ".join(f"tok{i}" for i in range(CONTENT_TOKEN_CAP - 2))
    text = f"{pad} pricing call"
    assert len(content_tokens(text)) == CONTENT_TOKEN_CAP
    score = score_deals(DealCorpus(days=[day]), _pred(day, claims=[_claim(text)]))
    assert score.summary.correct == 1


def test_score_deals_privacy_claim_text_and_snippet() -> None:
    bait = Frame("f-bait", "secret privacybait vault", skipped=True, skip_reason="excluded_app")
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1"), _seg("s-bait", NONE, "f-bait", bait=True)],
        frames={"f1": Frame("f1", "pricing call"), "f-bait": bait},
        skipped={"f-bait": bait},
    )
    text_leak = _pred(day, claims=[_claim("joined the pricing call privacybait")])
    snippet = _pred(
        day,
        claims=[
            PredictedClaim(
                "c1",
                "acme-q4",
                "what_happened",
                "joined the pricing call",
                [PredictedEvidence(segment_id="s1", snippet="secret privacybait vault")],
            )
        ],
    )
    assert score_deals(DealCorpus(days=[day]), text_leak).privacy_fail is True
    assert score_deals(DealCorpus(days=[day]), snippet).privacy_fail is True


def test_contradicts_different_slot_is_not_a_conflict() -> None:
    fact = GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call")},
        facts=[fact],
    )
    claim = _claim("Priya Chen", slot="who_involved")
    assert contradicts(claim, fact, day, "pricing call") is False


def test_fact_terms_satisfied_and_required_terms_edges() -> None:
    fact = GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))
    who = GoldFact("w", "acme-q4", "who_involved", (), {}, ("s1",), person_id="ghost")
    people = [PRIYA]
    assert fact_terms_satisfied("joined the pricing call", fact, people) is True
    assert fact_terms_satisfied("joined the call", fact, people) is False
    assert required_terms_present("Priya Natarajan", who, people) is False
    tagged = GoldFact(
        "t",
        "acme-q4",
        "what_happened",
        ("pricing", "call"),
        {},
        ("s1",),
        person_id="priya-natarajan",
    )
    assert fact_terms_satisfied("joined the pricing call", tagged, people) is True
    assert required_terms_present("Priya Natarajan", tagged, people) is False
    assert required_terms_present("joined the pricing call", tagged, people) is True


def test_matching_slot_facts_requires_same_slot_and_terms() -> None:
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call q4 deck")},
        facts=[
            GoldFact("f-call", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",)),
            GoldFact("f-deck", "acme-q4", "what_happened", ("q4", "deck"), {}, ("s1",)),
            GoldFact("f-next", "acme-q4", "next_step", ("pricing", "call"), {}, ("s1",)),
        ],
    )
    claim = _claim("joined the pricing call")
    hits = matching_slot_facts(claim, day)
    assert [fact.id for fact in hits] == ["f-call"]
    empty = matching_slot_facts(_claim("hello world"), day)
    assert empty == []


def test_honest_control_without_roster_and_deadline_is_false() -> None:
    gold = load_deal_corpus()
    empty = PredictedCorpus(
        days=[PredictedDay(name=day.name, segments=[], claims=[]) for day in gold.days]
    )
    assert honest_has_roster_name_and_deadline(gold, empty) is False
    no_date = PredictedCorpus(
        days=[
            PredictedDay(
                name=gold.days[0].name,
                segments=[],
                claims=[_claim("Priya Natarajan joined the pricing call")],
            )
        ]
    )
    assert honest_has_roster_name_and_deadline(gold, no_date) is False
    capital = PredictedCorpus(
        days=[
            PredictedDay(
                name=gold.days[0].name,
                segments=[],
                claims=[_claim("Jordan published Friday")],
            )
        ]
    )
    assert honest_has_roster_name_and_deadline(gold, capital) is False


def test_slot_modal_unknown_and_negated_will() -> None:
    next_fact = GoldFact("g", "acme-q4", "next_step", ("send", "pricing"), {}, ("s1",))
    what_fact = GoldFact("w", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))
    negated = PredictedClaim("c", "acme-q4", "next_step", "will not send pricing")
    unknown = PredictedClaim("c2", "acme-q4", "notes", "joined the pricing call")
    assert slot_modal_ok(negated, next_fact) is False
    assert slot_modal_ok(unknown, what_fact) is False
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "send pricing")},
        facts=[next_fact],
    )
    score = score_deals(
        DealCorpus(days=[day]),
        _pred(day, claims=[_claim("will not send pricing", slot="next_step")]),
    )
    assert score.summary.correct == 0


def test_score_deals_matched_deal_cite_off_deal_is_unsupported() -> None:
    day = _day(
        deals=[Deal("acme-q4", "Acme Q4", "Acme"), Deal("globex-pilot", "Globex", "Globex")],
        segments=[_seg("s1", "acme-q4", "f1"), _seg("s2", "globex-pilot", "f2")],
        frames={"f1": Frame("f1", "pricing call"), "f2": Frame("f2", "pricing call")},
        facts=[GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))],
    )
    predicted = _pred(
        day,
        claims=[
            PredictedClaim(
                "c1",
                "acme-q4",
                "what_happened",
                "joined the pricing call",
                [PredictedEvidence(frame_id="f2")],
            )
        ],
    )
    score = score_deals(DealCorpus(days=[day]), predicted)
    assert score.summary.correct == 0
    assert score.summary.unsupported == 1
    assert score.summary.accuracy == 0.0


def test_score_deals_date_form_alias_covers() -> None:
    fact = GoldFact(
        "g",
        "acme-q4",
        "what_happened",
        ("pricing", "call"),
        {"date": ("14 nov",)},
        ("s1",),
    )
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call 14 November")},
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    november = _pred(day, claims=[_claim("joined the pricing call before 14 November")])
    iso = _pred(day, claims=[_claim("joined the pricing call before 2026-11-14")])
    assert score_deals(gold, november).summary.correct == 1
    assert score_deals(gold, iso).summary.correct == 1


def test_claim_covers_and_unsupported_helpers() -> None:
    fact = GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1"), _seg("s2", "acme-q4", "f2")],
        frames={"f1": Frame("f1", "pricing call"), "f2": Frame("f2", "other")},
        facts=[fact],
    )
    pred = _pred(day, claims=[_claim("joined the pricing call")]).days[0]
    mapping = {"acme-q4": "acme-q4"}
    claim = pred.claims[0]
    assert _claim_covers(day, pred, mapping, claim, fact) is True
    wrong_slot = replace(claim, slot="next_step", text="will join the pricing call")
    assert _claim_covers(day, pred, mapping, wrong_slot, fact) is False
    wrong_deal = replace(claim, deal="other")
    assert _claim_covers(day, pred, mapping, wrong_deal, fact) is False
    will = replace(claim, text="will join the pricing call")
    assert _claim_covers(day, pred, mapping, will, fact) is False
    miss = replace(claim, evidence=[PredictedEvidence(segment_id="s2")])
    assert _unsupported(day, pred, mapping, miss, fact) is True
    who = GoldFact("w", "acme-q4", "who_involved", (), {}, ("s1",), person_id="priya-natarajan")
    who_claim = _claim("Priya Natarajan", slot="who_involved")
    who_pred = _pred(day, claims=[who_claim]).days[0]
    day_who = replace(
        day,
        facts=[who],
        frames={"f1": Frame("f1", "no roster here"), "f2": Frame("f2", "x")},
    )
    assert _unsupported(day_who, who_pred, mapping, who_pred.claims[0], who) is True


def test_pair_claims_one_claim_one_fact_keeps_the_break() -> None:
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call q4 deck")},
        facts=[
            GoldFact("f-call", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",)),
            GoldFact("f-deck", "acme-q4", "what_happened", ("q4", "deck"), {}, ("s1",)),
        ],
    )
    packed = _pred(day, claims=[_claim("joined the pricing call q4 deck")]).days[0]
    mapping = {"acme-q4": "acme-q4"}
    packed_pairs = _pair_claims(day, packed, mapping)
    assert len(packed_pairs) == 1
    two_cover = _pred(
        day,
        claims=[
            _claim("joined the pricing call", claim_id="c1"),
            _claim("attended the pricing call", claim_id="c2"),
        ],
    )
    two_pairs = _pair_claims(day, two_cover.days[0], mapping)
    assert len(two_pairs) == 1
    assert two_pairs[0][0].id == "c1"
    two_score = score_deals(DealCorpus(days=[day]), two_cover)
    assert two_score.summary.correct == 1
    assert two_score.summary.slot_tp["what_happened"] == 1


def test_pair_claims_ignores_the_wrong_deal() -> None:
    day = _day(
        deals=[Deal("acme-q4", "Acme Q4", "Acme"), Deal("globex-pilot", "Globex", "Globex")],
        segments=[_seg("s1", "acme-q4", "f1"), _seg("s2", "globex-pilot", "f2")],
        frames={"f1": Frame("f1", "pricing call"), "f2": Frame("f2", "q4 deck")},
        facts=[
            GoldFact("f-acme", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",)),
            GoldFact("f-globex", "globex-pilot", "what_happened", ("q4", "deck"), {}, ("s2",)),
        ],
    )
    gold = DealCorpus(days=[day])
    wrong = _pred(
        day,
        claims=[_claim("joined the pricing call", deal="globex-pilot", evidence="s2")],
    )
    score = score_deals(gold, wrong)
    assert score.summary.correct == 0
    pred = wrong.days[0]
    mapping = {"acme-q4": "acme-q4", "globex-pilot": "globex-pilot"}
    paired = _pair_claims(day, pred, mapping)
    assert paired == []
    assert _claim_covers(day, pred, mapping, pred.claims[0], day.facts[0]) is False
    split = _pred(day, claims=[_claim("joined the pricing call", deal="pred-a")])
    split.days[0].claims[0].deal = "pred-a"
    split_map = {"pred-a": "acme-q4", "pred-b": "acme-q4"}
    assert _pair_claims(day, split.days[0], split_map) == []


def test_contradicts_gates_on_slot_and_deal_evidence() -> None:
    people = [PRIYA, CHEN]
    what = GoldFact("f1", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))
    who = GoldFact("f2", "acme-q4", "who_involved", (), {}, ("s1",), person_id="priya-natarajan")
    day = _day(
        people=people,
        facts=[what, who],
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "Priya Natarajan pricing call")},
    )
    who_claim = _claim("Priya Chen", slot="who_involved")
    assert contradicts(who_claim, what, day, "Priya Natarajan pricing call") is False
    extra = _claim("Priya Natarajan joined the pricing call")
    assert contradicts(extra, what, day, "pricing call") is True
    assert contradicts(extra, what, day, "Priya Natarajan pricing call") is False


def test_company_cluster_does_not_read_deal_name() -> None:
    day = _day(
        deals=[Deal("northwind", "Zephyr", "Globex")],
        people=[],
        segments=[_seg("s1", "northwind", "f1", title="Zephyr inbox")],
        frames={"f1": Frame("f1", "notes")},
    )
    assert company_cluster(day.segments[0], day) == NONE
    titled = replace(day.segments[0], window_title="Globex inbox")
    assert company_cluster(titled, day) == "globex"


def test_deal_term_list_includes_roster_names() -> None:
    day = _day(
        facts=[
            GoldFact(
                "w",
                "acme-q4",
                "who_involved",
                (),
                {},
                ("s1",),
                person_id="priya-natarajan",
            ),
            GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",)),
        ]
    )
    terms = deal_term_list(day, "acme-q4")
    assert "Priya Natarajan" in terms
    assert "pricing" in terms


def test_stored_pipeline_texts_reads_frame_text() -> None:
    class _Rows(list[object]):
        def fetchall(self) -> list[object]:
            return list(self)

    class _Conn:
        def execute(self, sql: str, params: object = None) -> object:
            if "FROM segments" in sql and "ORDER BY started_at" in sql:
                return []
            if "FROM segment_frames" in sql:
                return []
            if "text_redacted FROM frame_text" in sql:
                return _Rows([("secret privacybait vault",)])
            return _Rows([])

    conn = _Conn()
    texts = _stored_pipeline_texts(conn)
    assert any("privacybait" in text for text in texts)
    predicted = collect_predicted_deals(conn, "day_x")
    assert any("privacybait" in text for text in predicted.stored_texts)


def test_validate_privacy_bait_legality_and_completeness() -> None:
    missing_skip = _day(
        segments=[_seg("s-bait", NONE, "f-bait", bait=True)],
        frames={"f-bait": Frame("f-bait", "secret privacybait vault")},
    )
    with pytest.raises(ValueError, match="not skipped"):
        validate_day(missing_skip)
    with pytest.raises(ValueError, match="not skipped"):
        privacy_bait_legal(DealCorpus(days=[missing_skip]))
    leftover = Frame("f-skip", "secret privacybait vault", skipped=True, skip_reason="excluded_app")
    incomplete = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call"), "f-skip": leftover},
        skipped={"f-skip": leftover},
        facts=[GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))],
    )
    with pytest.raises(ValueError, match="not on a gold segment"):
        validate_day(incomplete)
    excluded = Frame("f-ex", "vault", skipped=True, skip_reason="excluded_app")
    unlabeled = _day(
        segments=[_seg("s-ex", NONE, "f-ex")],
        frames={"f-ex": excluded},
        skipped={"f-ex": excluded},
    )
    with pytest.raises(ValueError, match="must set privacy_bait"):
        validate_day(unlabeled)
    empty_bait = _day(
        segments=[
            GoldSegment(id="s-bait", frame_ids=(), deal=NONE, privacy_bait=True),
        ]
    )
    with pytest.raises(ValueError, match="has no frames"):
        validate_day(empty_bait)


def test_extract_amounts_rejects_units_and_versions() -> None:
    assert "amt:5000000" not in extract_amounts("5m walk")
    assert "amt:4000" not in extract_amounts("4k monitor")
    assert extract_amounts("v1.2") == set()
    assert "amt:40000" in extract_amounts("40k")
    assert "amt:95000:usd" in extract_amounts("USD 95k")
    assert "amt:5000000:usd" in extract_amounts("$5m walk")
    assert "amt:40000:gbp" in extract_amounts("GBP 40k")
    assert "amt:12000:eur" in extract_amounts("EUR 12k")
    assert extract_amounts("USD 95k") != extract_amounts("95k")
    assert extract_amounts("GBP 40k") != extract_amounts("40k")


def test_semantic_collapse_keeps_priya_acme_pair_and_drops_near_duplicates() -> None:
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "Priya sent pricing to Acme deck")},
        facts=[
            GoldFact("f-priya", "acme-q4", "what_happened", ("priya", "pricing"), {}, ("s1",)),
            GoldFact("f-acme", "acme-q4", "what_happened", ("acme", "deck"), {}, ("s1",)),
        ],
    )
    gold = DealCorpus(days=[day])
    distinct = _pred(
        day,
        claims=[
            _claim("Priya sent pricing", claim_id="c1"),
            _claim("Acme deck landed", claim_id="c2"),
        ],
    )
    near = _pred(
        day,
        claims=[
            _claim("Priya pricing Acme deck", claim_id="c1"),
            _claim("Priya pricing Acme deck also", claim_id="c2"),
        ],
    )
    kept = score_deals(gold, distinct)
    dumped = score_deals(gold, near)
    assert kept.summary.correct == 2
    assert dumped.summary.correct == 1
    assert dumped.summary.claims == 2
    assert dumped.summary.what_happened_recall == 0.5


def test_is_multi_fact_claim_threshold_and_person_signature() -> None:
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call q4 deck")},
        facts=[
            GoldFact("f-call", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",)),
            GoldFact("f-deck", "acme-q4", "what_happened", ("q4", "deck"), {}, ("s1",)),
        ],
    )
    packed = _claim("joined the pricing call q4 deck")
    assert is_multi_fact_claim(packed, day) is True
    same_person = _day(
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
        ]
    )
    who = _claim("Priya Natarajan", slot="who_involved")
    assert is_multi_fact_claim(who, same_person) is False
    assert _fact_signature(same_person.facts[0])[1] == "priya-natarajan"


def test_score_deals_next_step_without_commitment_is_rejected() -> None:
    fact = GoldFact("g", "acme-q4", "next_step", ("pricing",), {}, ("s1",))
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing Friday")},
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    score = score_deals(gold, _pred(day, claims=[_claim("pricing Friday", slot="next_step")]))
    plain = PredictedClaim("c", "acme-q4", "next_step", "pricing Friday")
    assert slot_modal_ok(plain, fact) is False
    assert score.summary.correct == 0


def test_align_segments_tiebreak_started_at_before_id() -> None:
    day = _day(segments=[_seg("s1", "acme-q4", "f1", "f2")])
    late = PredictedSegment("a-late", ("f1", "f2"), "late", started_at="2026-01-01T10:00:00Z")
    early = PredictedSegment("z-early", ("f1", "f2"), "early", started_at="2026-01-01T09:00:00Z")
    labels = align_segments(day, [late, early])
    assert labels["s1"] == "early"


def test_match_clusters_equal_overlap_prefers_gold_id() -> None:
    day = _day(
        deals=[Deal("b-deal", "B"), Deal("a-deal", "A")],
        segments=[_seg("s1", "a-deal", "f1"), _seg("s2", "b-deal", "f2")],
    )
    mapping = match_clusters(day, {"s1": "p1", "s2": "p1"})
    assert mapping["p1"] == "a-deal"


def test_contradicts_needs_deal_ok_not_just_evidence() -> None:
    what = GoldFact("f1", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))
    who = GoldFact("f2", "acme-q4", "who_involved", (), {}, ("s1",), person_id="priya-natarajan")
    day = _day(
        people=[PRIYA, CHEN],
        facts=[what, who],
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "Priya Chen pricing call")},
    )
    claim = _claim("Priya Chen joined the pricing call")
    assert contradicts(claim, what, day, "Priya Chen pricing call") is True
    gold = DealCorpus(days=[day])
    score = score_deals(gold, _pred(day, claims=[claim]))
    assert score.summary.correct == 0

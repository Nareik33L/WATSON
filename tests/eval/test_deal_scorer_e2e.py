"""Every §11.2 scorer rule, exercised end-to-end through score_deals."""

from __future__ import annotations

from dataclasses import replace

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
    honest_prediction,
    load_deal_corpus,
    score_deals,
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
        ("pricing", "call", "14 nov"),
        {},
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


def test_score_deals_bare_list_never_matches() -> None:
    fact = GoldFact("g", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",))
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call")},
        facts=[fact],
    )
    gold = DealCorpus(days=[day])
    for text in (
        "pricing call",
        "will pricing call",
        "need to pricing call",
        "pricing, call",
    ):
        score = score_deals(gold, _pred(day, claims=[_claim(text)]))
        assert score.summary.correct == 0, text
    score = score_deals(gold, _pred(day, claims=[_claim("joined the pricing call")]))
    assert score.summary.correct == 1


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


def test_score_deals_honest_control_on_gold() -> None:
    gold = load_deal_corpus()
    score = score_deals(gold, honest_prediction(gold))
    assert score.grouping.accuracy == 1.0
    assert score.summary.accuracy >= 0.85
    assert score.summary.unsupported == 0
    assert not score.privacy_fail


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

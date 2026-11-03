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
    _claim_covers,
    _unsupported,
    content_tokens,
    contradicts,
    extract_amounts,
    fact_terms_satisfied,
    honest_prediction,
    honest_prediction_filled,
    load_deal_corpus,
    required_terms_present,
    resolve_person_id,
    score_deals,
    slot_modal_ok,
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
    assert extract_amounts("$90k") == {"amt:90000:usd"}
    assert extract_amounts("$9k") == {"amt:9000:usd"}
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
    assert extract_amounts("$1m") == {"amt:1000000:usd"}
    assert extract_amounts("$10m") == {"amt:10000000:usd"}
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


def test_score_deals_multi_fact_claim_matches_none() -> None:
    day = _day(
        segments=[_seg("s1", "acme-q4", "f1")],
        frames={"f1": Frame("f1", "pricing call q4 deck")},
        facts=[
            GoldFact("f-call", "acme-q4", "what_happened", ("pricing", "call"), {}, ("s1",)),
            GoldFact("f-deck", "acme-q4", "what_happened", ("q4", "deck"), {}, ("s1",)),
        ],
    )
    gold = DealCorpus(days=[day])
    dump = _pred(day, claims=[_claim("pricing call q4 deck")])
    score = score_deals(gold, dump)
    assert score.summary.correct == 0
    assert score.summary.what_happened_recall == 0.0
    assert score.summary.precision == 0.0
    one = _pred(day, claims=[_claim("joined the pricing call")])
    assert score_deals(gold, one).summary.correct == 1


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
    bare = score_deals(gold, honest_prediction(gold))
    filled = score_deals(gold, honest_prediction_filled(gold))
    for score in (bare, filled):
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


def test_extract_amounts_currency_forms() -> None:
    assert extract_amounts("$90k") == {"amt:90000:usd"}
    assert extract_amounts("$1m") == {"amt:1000000:usd"}
    assert extract_amounts("$1.2M") == {"amt:1200000:usd"}
    assert extract_amounts("$40,000") == {"amt:40000:usd"}
    assert extract_amounts("£40k") == {"amt:40000:gbp"}
    assert "amt:40" not in extract_amounts("$40,000")
    assert "amt:0" not in extract_amounts("$40,000")
    assert extract_amounts("$90k") != extract_amounts("$9k")
    assert extract_amounts("$1m") != extract_amounts("$10m")
    assert extract_amounts("Send $90k and £40k") == {"amt:90000:usd", "amt:40000:gbp"}
    assert extract_amounts("10") == {"amt:10"}
    assert extract_amounts("40") == {"amt:40"}
    assert "amt:4" not in extract_amounts("q4")


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

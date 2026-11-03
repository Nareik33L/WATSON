"""Authoritative §11.2 fixture-validation test (M3f). No model."""

from __future__ import annotations

from pathlib import Path

import pytest

from watson.intelligence.eval.deals import (
    CONTENT_TOKEN_CAP,
    SLOTS,
    baseline_all_none,
    baseline_empty_summary,
    baseline_group_by_app,
    baseline_group_by_company,
    baseline_keyword_dump_comma_list,
    baseline_keyword_dump_duplicate_need_to,
    baseline_keyword_dump_duplicate_what,
    baseline_keyword_dump_duplicate_what_2ch,
    baseline_keyword_dump_duplicate_what_3ch,
    baseline_keyword_dump_duplicate_what_filled,
    baseline_keyword_dump_extra_what,
    baseline_keyword_dump_extra_what_will_next,
    baseline_keyword_dump_need_to,
    baseline_keyword_dump_over_cap,
    baseline_keyword_dump_packed,
    baseline_keyword_dump_please,
    baseline_keyword_dump_will_prefix,
    baseline_keyword_dump_within_cap,
    baseline_keyword_dump_within_cap_will_next,
    baseline_one_deal_corpus,
    baseline_one_deal_per_day,
    baseline_one_deal_per_segment,
    content_tokens,
    corpus_guidance,
    dump_windows,
    honest_has_roster_name_and_deadline,
    honest_prediction,
    honest_prediction_filled,
    load_deal_corpus,
    load_failing_acme_1211,
    load_v1_deal_corpus,
    privacy_bait_legal,
    privacy_leak,
    score_deals,
    validate_corpus,
)

REPO = Path(__file__).resolve().parents[2]
DEAL_GOLD = REPO / "tests" / "fixtures" / "deal_gold"
GATE = 0.85


@pytest.fixture(scope="module")
def gold():
    corpus = load_deal_corpus(DEAL_GOLD)
    validate_corpus(corpus)
    privacy_bait_legal(corpus)
    return corpus


def test_v1_guidance_floors_and_dump_shape(gold) -> None:
    counts = corpus_guidance(gold)
    assert counts["days"] == 5
    assert counts["deals"] >= 8
    assert counts["multi_touch"] >= 3
    assert counts["same_company_pairs"] >= 4
    assert counts["lookalike_pairs"] >= 2
    assert counts["noise_rate"] >= 0.20
    assert counts["privacy_bait"] >= 1
    assert counts["what_facts_per_deal_day"] >= 1.5


def test_negative_grouping_baselines_below_gate(gold) -> None:
    rows = {
        "all-none": score_deals(gold, baseline_all_none(gold)).grouping.accuracy,
        "one-deal-per-day": score_deals(gold, baseline_one_deal_per_day(gold)).grouping.accuracy,
        "one-deal-corpus": score_deals(gold, baseline_one_deal_corpus(gold)).grouping.accuracy,
        "one-deal-per-segment": score_deals(
            gold, baseline_one_deal_per_segment(gold)
        ).grouping.accuracy,
        "group-by-app": score_deals(gold, baseline_group_by_app(gold)).grouping.accuracy,
        "group-by-company": score_deals(gold, baseline_group_by_company(gold)).grouping.accuracy,
    }
    for name, score in rows.items():
        assert score < GATE, f"{name} grouping {score:.3f} must be < {GATE}"


def test_negative_summary_baselines_below_gate(gold) -> None:
    rows = {
        "empty-summary": score_deals(gold, baseline_empty_summary(gold)).summary.accuracy,
        "keyword-dump-over-cap": score_deals(
            gold, baseline_keyword_dump_over_cap(gold)
        ).summary.accuracy,
        "keyword-dump-within-cap": score_deals(
            gold, baseline_keyword_dump_within_cap(gold)
        ).summary.accuracy,
        "keyword-dump-please": score_deals(
            gold, baseline_keyword_dump_please(gold)
        ).summary.accuracy,
        "keyword-dump-packed": score_deals(
            gold, baseline_keyword_dump_packed(gold)
        ).summary.accuracy,
        "keyword-dump-will-prefix": score_deals(
            gold, baseline_keyword_dump_will_prefix(gold)
        ).summary.accuracy,
        "keyword-dump-extra-what": score_deals(
            gold, baseline_keyword_dump_extra_what(gold)
        ).summary.accuracy,
        "keyword-dump-need-to": score_deals(
            gold, baseline_keyword_dump_need_to(gold)
        ).summary.accuracy,
        "keyword-dump-comma-list": score_deals(
            gold, baseline_keyword_dump_comma_list(gold)
        ).summary.accuracy,
        "keyword-dump-duplicate-what": score_deals(
            gold, baseline_keyword_dump_duplicate_what(gold)
        ).summary.accuracy,
        "keyword-dump-duplicate-need-to": score_deals(
            gold, baseline_keyword_dump_duplicate_need_to(gold)
        ).summary.accuracy,
        "keyword-dump-duplicate-what-3ch": score_deals(
            gold, baseline_keyword_dump_duplicate_what_3ch(gold)
        ).summary.accuracy,
        "keyword-dump-duplicate-what-2ch": score_deals(
            gold, baseline_keyword_dump_duplicate_what_2ch(gold)
        ).summary.accuracy,
        "keyword-dump-within-cap-will-next": score_deals(
            gold, baseline_keyword_dump_within_cap_will_next(gold)
        ).summary.accuracy,
        "keyword-dump-extra-what-will-next": score_deals(
            gold, baseline_keyword_dump_extra_what_will_next(gold)
        ).summary.accuracy,
    }
    for filler in ("please", "still", "revised", "discussed"):
        rows[f"keyword-dump-duplicate-{filler}"] = score_deals(
            gold, baseline_keyword_dump_duplicate_what_filled(gold, filler)
        ).summary.accuracy
    for name, score in rows.items():
        assert score < GATE, f"{name} summary {score:.3f} must be < {GATE}"


def test_honest_summary_meets_gate_without_and_with_fillers(gold) -> None:
    bare = honest_prediction(gold, DEAL_GOLD)
    filled = honest_prediction_filled(gold, DEAL_GOLD)
    for name, predicted in (("honest", bare), ("honest-filled", filled)):
        score = score_deals(gold, predicted)
        assert score.grouping.accuracy == 1.0, name
        assert score.summary.unsupported == 0, name
        assert score.summary.accuracy == pytest.approx(1.0), (
            f"{name} summary {score.summary.accuracy:.3f}"
        )
        assert not score.privacy_fail, name
        assert honest_has_roster_name_and_deadline(gold, predicted)
    assert any(
        "please" in claim.text.casefold()
        for day in filled.days
        for claim in day.claims
        if claim.slot == "next_step"
    )
    assert not any(
        "please" in claim.text.casefold()
        for day in bare.days
        for claim in day.claims
        if claim.slot == "next_step"
    )


def test_privacy_bait_absent_from_stored_outputs(gold) -> None:
    outputs = [
        baseline_all_none(gold),
        baseline_one_deal_per_day(gold),
        baseline_one_deal_corpus(gold),
        baseline_one_deal_per_segment(gold),
        baseline_group_by_app(gold),
        baseline_group_by_company(gold),
        baseline_empty_summary(gold),
        baseline_keyword_dump_over_cap(gold),
        baseline_keyword_dump_within_cap(gold),
        baseline_keyword_dump_packed(gold),
        baseline_keyword_dump_please(gold),
        baseline_keyword_dump_will_prefix(gold),
        baseline_keyword_dump_extra_what(gold),
        baseline_keyword_dump_duplicate_what(gold),
        baseline_keyword_dump_within_cap_will_next(gold),
        honest_prediction(gold, DEAL_GOLD),
        honest_prediction_filled(gold, DEAL_GOLD),
    ]
    for predicted in outputs:
        assert not privacy_leak(gold, predicted)
        for day in predicted.days:
            for claim in day.claims:
                assert "privacybait" not in claim.text.casefold()
                for evidence in claim.evidence:
                    assert evidence.frame_id is None or "password" not in (evidence.frame_id or "")


def test_acme_1211_group_by_company_is_a_failing_shape() -> None:
    failing = load_failing_acme_1211(DEAL_GOLD)
    validate_corpus(failing)
    score = score_deals(failing, baseline_group_by_company(failing))
    assert score.grouping.pairwise_f1 == pytest.approx(0.905, abs=0.001)
    assert score.grouping.accuracy >= GATE
    assert score.grouping.assignment_accuracy == pytest.approx(0.95, abs=0.001)
    assert score.grouping.deal_segment_recall == pytest.approx(0.9375, abs=0.001)


def test_over_cap_dump_exceeds_token_cap(gold) -> None:
    predicted = baseline_keyword_dump_over_cap(gold)
    over = [
        claim
        for day in predicted.days
        for claim in day.claims
        if len(content_tokens(claim.text)) > CONTENT_TOKEN_CAP
    ]
    assert over, "over-cap dump must emit at least one >24-token claim"


def test_within_cap_windows_are_atomic(gold) -> None:
    from watson.intelligence.eval.deals import deal_term_list
    from watson.intelligence.eval.metrics import normalize

    for day in gold.days:
        for deal in day.deals:
            terms = deal_term_list(day, deal.id)
            for window in dump_windows(terms):
                assert len(content_tokens(window)) <= CONTENT_TOKEN_CAP
                for term in terms:
                    phrase = normalize(term)
                    if " " not in phrase:
                        continue
                    pieces = phrase.split()
                    if any(piece in window.split() for piece in pieces):
                        assert phrase in window, f"split multi-word term {phrase!r} in {window!r}"


def test_baseline_score_table(gold) -> None:
    """Print the numbers the PR must report. Not a second gate."""
    grouping = [
        ("all-none", score_deals(gold, baseline_all_none(gold)).grouping),
        ("one-deal-per-day", score_deals(gold, baseline_one_deal_per_day(gold)).grouping),
        ("one-deal-corpus", score_deals(gold, baseline_one_deal_corpus(gold)).grouping),
        (
            "one-deal-per-segment",
            score_deals(gold, baseline_one_deal_per_segment(gold)).grouping,
        ),
        ("group-by-app", score_deals(gold, baseline_group_by_app(gold)).grouping),
        ("group-by-company", score_deals(gold, baseline_group_by_company(gold)).grouping),
    ]
    summary = [
        ("empty-summary", score_deals(gold, baseline_empty_summary(gold)).summary),
        (
            "keyword-dump-over-cap",
            score_deals(gold, baseline_keyword_dump_over_cap(gold)).summary,
        ),
        (
            "keyword-dump-within-cap",
            score_deals(gold, baseline_keyword_dump_within_cap(gold)).summary,
        ),
        (
            "keyword-dump-packed",
            score_deals(gold, baseline_keyword_dump_packed(gold)).summary,
        ),
        (
            "keyword-dump-please",
            score_deals(gold, baseline_keyword_dump_please(gold)).summary,
        ),
        (
            "keyword-dump-will-prefix",
            score_deals(gold, baseline_keyword_dump_will_prefix(gold)).summary,
        ),
        (
            "keyword-dump-extra-what",
            score_deals(gold, baseline_keyword_dump_extra_what(gold)).summary,
        ),
        (
            "keyword-dump-need-to",
            score_deals(gold, baseline_keyword_dump_need_to(gold)).summary,
        ),
        (
            "keyword-dump-comma-list",
            score_deals(gold, baseline_keyword_dump_comma_list(gold)).summary,
        ),
        (
            "keyword-dump-duplicate-what",
            score_deals(gold, baseline_keyword_dump_duplicate_what(gold)).summary,
        ),
        (
            "keyword-dump-duplicate-need-to",
            score_deals(gold, baseline_keyword_dump_duplicate_need_to(gold)).summary,
        ),
        (
            "keyword-dump-within-cap-will-next",
            score_deals(gold, baseline_keyword_dump_within_cap_will_next(gold)).summary,
        ),
        (
            "keyword-dump-extra-what-will-next",
            score_deals(gold, baseline_keyword_dump_extra_what_will_next(gold)).summary,
        ),
        ("honest-summary", score_deals(gold, honest_prediction(gold, DEAL_GOLD)).summary),
        (
            "honest-summary-filled",
            score_deals(gold, honest_prediction_filled(gold, DEAL_GOLD)).summary,
        ),
    ]
    print("baseline score table")
    print(f"{'baseline':<24} {'metric':<22} {'score':>8}")
    for name, row in grouping:
        print(f"{name:<24} {'grouping':<22} {row.accuracy:8.3f}")
    for name, row in summary:
        print(f"{name:<24} {'summary':<22} {row.accuracy:8.3f}")
    failing = load_failing_acme_1211(DEAL_GOLD)
    fail_score = score_deals(failing, baseline_group_by_company(failing)).grouping
    print(
        f"{'acme-12/1/1/1 company':<24} {'grouping':<22} {fail_score.accuracy:8.3f}"
        f"  (P={fail_score.pairwise_precision:.3f} F1={fail_score.pairwise_f1:.3f})"
    )


def test_within_cap_dump_emits_each_window_in_every_slot(gold) -> None:
    predicted = baseline_keyword_dump_within_cap(gold)
    checked = 0
    for day in predicted.days:
        by_key: dict[tuple[str, str], set[str]] = {}
        for claim in day.claims:
            assert len(content_tokens(claim.text)) <= CONTENT_TOKEN_CAP, claim.text
            by_key.setdefault((claim.deal, claim.text), set()).add(claim.slot)
            checked += 1
        for slots in by_key.values():
            assert slots == set(SLOTS)
    assert checked >= 10


def test_v1_pipeline_overlays_use_fixture_frame_ids() -> None:
    import uuid

    overlays = load_v1_deal_corpus(REPO / "tests" / "fixtures")
    validate_corpus(overlays)
    privacy_bait_legal(overlays)
    assert {day.name for day in overlays.days} == {
        "day_acme_q4",
        "day_fragmented_multi",
        "day_meeting_heavy",
        "day_sensitive_traps",
        "day_near_empty",
    }
    for day in overlays.days:
        for segment in day.segments:
            if segment.privacy_bait:
                continue
            assert segment.frame_ids
            for frame_id in segment.frame_ids:
                parsed = uuid.UUID(frame_id)
                assert parsed.version == 5

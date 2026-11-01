"""Citation verifier: strip invalid/foreign ids; uncited claims → Unverified."""

from __future__ import annotations

from watson.intelligence.citations import (
    citations_resolvable,
    expand_index_citations,
    verify_narrative,
)

DAY_A = "11111111-1111-4111-8111-111111111111"
DAY_B = "22222222-2222-4222-8222-222222222222"
MISSING = "33333333-3333-4333-8333-333333333333"

CLAIMS = (
    {"id": DAY_A, "kind": "outcome", "assertion": "observed", "text": "Appendix drafted"},
    {
        "id": "44444444-4444-4444-8444-444444444444",
        "kind": "activity",
        "assertion": "observed",
        "text": "Editing",
    },
)


def test_strips_missing_and_foreign_citations() -> None:
    paragraphs = [
        {
            "text": (
                f"Valid work landed [c:{DAY_A}]. "
                f"A ghost sentence cites nothing [c:{MISSING}]. "
                f"A foreign claim leaked [c:{DAY_B}]."
            ),
            "claim_ids": [DAY_A, MISSING, DAY_B],
        }
    ]
    verified = verify_narrative(paragraphs, list(CLAIMS))
    assert f"[c:{DAY_A}]" in verified.narrative_md
    assert MISSING not in verified.narrative_md
    assert DAY_B not in verified.narrative_md
    assert verified.claim_ids == (DAY_A,)
    assert citations_resolvable(verified.narrative_md, {DAY_A, CLAIMS[1]["id"]})
    unverified_ids = {row["id"] for row in verified.unverified}
    assert CLAIMS[1]["id"] in unverified_ids
    assert DAY_A not in unverified_ids
    assert DAY_B not in unverified_ids
    assert MISSING not in unverified_ids


def test_uncited_claims_go_to_unverified() -> None:
    paragraphs = [{"text": f"Only the outcome [c:{DAY_A}].", "claim_ids": [DAY_A]}]
    verified = verify_narrative(paragraphs, list(CLAIMS))
    assert verified.unverified == ({"id": CLAIMS[1]["id"], "kind": "activity", "text": "Editing"},)


def test_empty_citation_is_invalid() -> None:
    paragraphs = [{"text": f"Broken cite [c:]. Keep this [c:{DAY_A}].", "claim_ids": [DAY_A]}]
    verified = verify_narrative(paragraphs, list(CLAIMS))
    assert "[c:]" not in verified.narrative_md
    assert f"[c:{DAY_A}]" in verified.narrative_md


def test_citations_resolvable_rejects_foreign_or_empty() -> None:
    assert citations_resolvable(f"[c:{DAY_A}]", {DAY_A}) is True
    assert citations_resolvable(f"[c:{DAY_B}]", {DAY_A}) is False
    assert citations_resolvable("[c:]", {DAY_A}) is False
    assert citations_resolvable("no citations", {DAY_A}) is True


def test_expand_index_citations_maps_one_based_ids() -> None:
    expanded = expand_index_citations(
        [{"text": "Drafted [c:1].", "claim_ids": ["1"]}],
        list(CLAIMS),
    )
    assert expanded[0]["text"] == f"Drafted [c:{DAY_A}]."
    assert expanded[0]["claim_ids"] == [DAY_A]
    verified = verify_narrative(expanded, list(CLAIMS))
    assert verified.claim_ids == (DAY_A,)

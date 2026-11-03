"""Named-mutation score for the deal scorer. Target ≥ 80% of 68 mutants."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DEALS = "src/watson/intelligence/eval/deals.py"
DEAL_GOLD = REPO / "tests" / "fixtures" / "deal_gold"
TARGET_MUTANTS = 68
KILL_FLOOR = 55  # 80% of 68
TEST_PATHS = (
    "tests/eval/test_deal_scorer_e2e.py",
    "tests/unit/test_deal_metrics.py",
    "tests/eval/test_fixture_validation.py",
)
_COLLECTED = re.compile(r"(\d+) tests? collected")
_RAN = re.compile(r"(\d+) (passed|failed)")

# QA's remaining survivors first, then the rest of the 68-mutant set.
MUTANTS: tuple[tuple[str, str, str], ...] = (
    (
        "stored-empty",
        '        blobs.extend(str(row[0] or "") for row in rows)\n    return blobs\n',
        '        blobs.extend(str(row[0] or "") for row in rows)\n    return []\n',
    ),
    (
        "name-any-capital",
        "            if any(has_phrase(claim.text, name) for name in roster):\n",
        (
            "            if any(part[:1].isupper() for part in claim.text.split()"
            " if part[:1].isalpha()):\n"
        ),
    ),
    (
        "hedges-off",
        "        if tokens & NEXT_HEDGES or tokens & NEXT_NEGATION:\n",
        "        if tokens & NEXT_NEGATION:\n",
    ),
    (
        "alias-gt3",
        "            if len(phrases) > 3:\n",
        "            if False and len(phrases) > 3:\n",
    ),
    (
        "alias-stopword",
        "                if not content_tokens(phrase):\n",
        "                if False and not content_tokens(phrase):\n",
    ),
    (
        "alias-cross-deal",
        "                if norm in other_deals:\n",
        "                if False and norm in other_deals:\n",
    ),
    (
        "pair-no-break",
        (
            "            if _claim_covers(day, pred, mapping, claim, fact):\n"
            "                used.add(claim.id)\n"
            "                paired.append((claim, fact))\n"
            "                break\n"
        ),
        (
            "            if _claim_covers(day, pred, mapping, claim, fact):\n"
            "                used.add(claim.id)\n"
            "                paired.append((claim, fact))\n"
        ),
    ),
    (
        "pair-ignore-deal",
        "            if claim.id in used or claim.deal != pred_id or claim.slot != fact.slot:\n",
        "            if claim.id in used or claim.slot != fact.slot:\n",
    ),
    (
        "contradict-slot",
        "    if claim.slot != fact.slot:\n        return False\n",
        "    if False:\n        return False\n",
    ),
    (
        "contradict-gate",
        "            if deal_ok and evidence_ok:\n                continue\n",
        "            if False:\n                continue\n",
    ),
    (
        "bait-legality",
        "            if skip is None or skip.skip_reason not in EXCLUDED_REASONS:\n",
        "            if False:\n",
    ),
    (
        "bait-complete",
        (
            "    leftover = skipped_excluded - {\n"
            "        frame_id for segment in day.segments for frame_id in segment.frame_ids\n"
            "    }\n"
            "    if leftover:\n"
        ),
        (
            "    leftover = skipped_excluded - {\n"
            "        frame_id for segment in day.segments for frame_id in segment.frame_ids\n"
            "    }\n"
            "    if False and leftover:\n"
        ),
    ),
    (
        "company-deal-name",
        "        for deal in item.deals:\n            company = deal.company.strip()\n",
        "        for deal in item.deals:\n            company = deal.name.strip()\n",
    ),
    (
        "dump-omit-names",
        "            if person is not None:\n                terms.append(person.name)\n",
        "            if person is not None:\n                pass\n",
    ),
    (
        "currency-word",
        '        currency = _CURRENCY_CODE.get(sym or "") or _CURRENCY_WORD.get(ccy_word)\n',
        '        currency = _CURRENCY_CODE.get(sym or "")\n',
    ),
    (
        "collapse-skip",
        "        if key in seen:\n            continue\n",
        "        if False and key in seen:\n            continue\n",
    ),
    (
        "near-fillers-empty",
        '_NEAR_FILLERS = frozenset({"please", "still", "revised", "discussed"})\n',
        "_NEAR_FILLERS = frozenset()\n",
    ),
    (
        "amount-false-unit",
        "        if not currency and suf and _amount_false_unit(leftover, match.end()):\n",
        (
            "        if False and not currency and suf"
            " and _amount_false_unit(leftover, match.end()):\n"
        ),
    ),
    (
        "version-strip",
        '    leftover = _VERSION.sub(" ", text)\n',
        "    leftover = text\n",
    ),
    (
        "what-future",
        "        if tokens & WHAT_FUTURE:\n            return False\n",
        "        if False and tokens & WHAT_FUTURE:\n            return False\n",
    ),
    (
        "token-cap",
        "    if len(content_tokens(claim.text)) > CONTENT_TOKEN_CAP:\n        return False\n",
        "    if False:\n        return False\n",
    ),
    (
        "covers-deal",
        "    if mapping.get(claim.deal) != fact.deal_id:\n        return False\n",
        "    if False and mapping.get(claim.deal) != fact.deal_id:\n        return False\n",
    ),
    (
        "covers-modal",
        "    if not slot_modal_ok(claim, fact):\n        return False\n",
        "    if False and not slot_modal_ok(claim, fact):\n        return False\n",
    ),
    (
        "covers-contradict",
        "    if contradicts(claim, fact, day, cited_text):\n        return False\n",
        "    if False and contradicts(claim, fact, day, cited_text):\n        return False\n",
    ),
    (
        "empty-precision",
        "        precision = 1.0 if gold_fact_total == 0 else 0.0\n",
        "        precision = 1.0\n",
    ),
    (
        "privacy-stored",
        "        for text in pred.stored_texts:\n",
        "        for text in []:\n",
    ),
    (
        "bait-unlabeled",
        "            if segment.frame_ids and set(segment.frame_ids) <= skipped_excluded:\n",
        (
            "            if False and segment.frame_ids"
            " and set(segment.frame_ids) <= skipped_excluded:\n"
        ),
    ),
    (
        "alias-weekday",
        "                if _weekday_date_alias(term, phrase):\n",
        "                if False and _weekday_date_alias(term, phrase):\n",
    ),
    (
        "alias-exclusive",
        "                if _exclusive_other_term(norm, fact_terms, same_deal):\n",
        "                if False and _exclusive_other_term(norm, fact_terms, same_deal):\n",
    ),
    (
        "multi-fact-ge2",
        "    return len({_fact_signature(fact) for fact in hits}) >= 2\n",
        "    return len({_fact_signature(fact) for fact in hits}) >= 3\n",
    ),
    (
        "honest-slot",
        '            if claim.slot != "what_happened":\n                continue\n',
        '            if False and claim.slot != "what_happened":\n                continue\n',
    ),
    (
        "honest-dates",
        "            if not extract_dates(claim.text):\n                continue\n",
        "            if False and not extract_dates(claim.text):\n                continue\n",
    ),
    (
        "hedges-and",
        "        if tokens & NEXT_HEDGES or tokens & NEXT_NEGATION:\n",
        "        if tokens & NEXT_HEDGES and tokens & NEXT_NEGATION:\n",
    ),
    (
        "commit-and-imperative",
        "        return has_commit or imperative\n",
        "        return has_commit and imperative\n",
    ),
    (
        "what-phrases",
        "        return not any(has_phrase(body, phrase) for phrase in WHAT_FUTURE_PHRASES)\n",
        "        return True\n",
    ),
    (
        "leftover-digit",
        '            out.add(f"amt:{int(token)}")\n',
        "            pass\n",
    ),
    (
        "currency-or-and",
        '        currency = _CURRENCY_CODE.get(sym or "") or _CURRENCY_WORD.get(ccy_word)\n',
        ('        currency = _CURRENCY_CODE.get(sym or "") and _CURRENCY_WORD.get(ccy_word)\n'),
    ),
    (
        "privacy-claim-text",
        "            if _contains_marker(claim.text, bait_markers):\n                return True\n",
        (
            "            if False and _contains_marker(claim.text, bait_markers):\n"
            "                return True\n"
        ),
    ),
    (
        "assignment-none",
        "    if gold_deal == NONE:\n        return pred == NONE\n",
        "    if False and gold_deal == NONE:\n        return pred == NONE\n",
    ),
    (
        "assignment-missing",
        "    if pred in {NONE, MISSING}:\n        return False\n",
        "    if False and pred in {NONE, MISSING}:\n        return False\n",
    ),
    (
        "pair-ignore-slot",
        "            if claim.id in used or claim.deal != pred_id or claim.slot != fact.slot:\n",
        "            if claim.id in used or claim.deal != pred_id:\n",
    ),
    (
        "pair-skip-used",
        "            if claim.id in used or claim.deal != pred_id or claim.slot != fact.slot:\n",
        "            if claim.deal != pred_id or claim.slot != fact.slot:\n",
    ),
    (
        "covers-terms",
        "    if not fact_terms_satisfied(claim.text, fact, day.people):\n        return False\n",
        (
            "    if False and not fact_terms_satisfied(claim.text, fact, day.people):\n"
            "        return False\n"
        ),
    ),
    (
        "covers-evidence",
        "    if not set(cited_segments) & set(fact.evidence_segments):\n        return False\n",
        (
            "    if False and not set(cited_segments) & set(fact.evidence_segments):\n"
            "        return False\n"
        ),
    ),
    (
        "covers-frames",
        "    if frames is None:\n        return False\n",
        "    if False and frames is None:\n        return False\n",
    ),
    (
        "fact-values-continue",
        "            if value in fact_values[kind]:\n                continue\n",
        "            if False and value in fact_values[kind]:\n                continue\n",
    ),
    (
        "deal-ok-or",
        "            if deal_ok and evidence_ok:\n                continue\n",
        "            if deal_ok or evidence_ok:\n                continue\n",
    ),
    (
        "who-person-match",
        "        return person_entities_match(text, fact.person_id, people)\n",
        "        return True\n",
    ),
    (
        "matching-slot",
        (
            "        if fact.slot == claim.slot"
            " and required_terms_present(claim.text, fact, day.people)\n"
        ),
        "        if required_terms_present(claim.text, fact, day.people)\n",
    ),
    (
        "company-no-match",
        "    if not matches:\n        return NONE\n",
        "    if not matches:\n        return normalize(segment.window_title) or NONE\n",
    ),
    (
        "privacy-segment-deal",
        '            if segment.deal in {NONE, MISSING, ""}:\n                continue\n',
        (
            '            if False and segment.deal in {NONE, MISSING, ""}:\n'
            "                continue\n"
        ),
    ),
    (
        "packed-skip-multi",
        "        if not is_multi_fact_claim(claim, day):\n            continue\n",
        "        if False and not is_multi_fact_claim(claim, day):\n            continue\n",
    ),
    (
        "extra-what-channels",
        (
            "        channels = {segment.channel for segment in _deal_segments(day, deal.id)}\n"
            '        if not {"email", "call", "doc"} <= channels:\n'
            "            continue\n"
            "        segs = _deal_segments(day, deal.id)\n"
            "        evidence = [PredictedEvidence(segment_id=segment.id) for segment in segs]\n"
            "        windows = dump_windows(deal_term_list(day, deal.id))\n"
            "        for window_index, window in enumerate(windows):\n"
            "            extras.append(\n"
            "                PredictedClaim(\n"
            '                    id=f"dump-extra-what-{day.name}-{deal.id}-{window_index}",\n'
        ),
        (
            "        channels = {segment.channel for segment in _deal_segments(day, deal.id)}\n"
            '        if False and not {"email", "call", "doc"} <= channels:\n'
            "            continue\n"
            "        segs = _deal_segments(day, deal.id)\n"
            "        evidence = [PredictedEvidence(segment_id=segment.id) for segment in segs]\n"
            "        windows = dump_windows(deal_term_list(day, deal.id))\n"
            "        for window_index, window in enumerate(windows):\n"
            "            extras.append(\n"
            "                PredictedClaim(\n"
            '                    id=f"dump-extra-what-{day.name}-{deal.id}-{window_index}",\n'
        ),
    ),
    (
        "duplicate-copies",
        "        copies = 2 if duplicate else 1\n",
        "        copies = 1\n",
    ),
    (
        "unsupported-zero",
        "    if unsupported > 0:\n        accuracy = 0.0\n",
        "    if False and unsupported > 0:\n        accuracy = 0.0\n",
    ),
    (
        "next-negation",
        'NEXT_NEGATION = frozenset({"not", "never", "wont"})\n',
        "NEXT_NEGATION = frozenset()\n",
    ),
    (
        "done-form",
        "        if _done_form_without_planned(tokens):\n            return False\n",
        "        if False and _done_form_without_planned(tokens):\n            return False\n",
    ),
    (
        "resolve-named-empty",
        "    if not named:\n        return None\n",
        "    if False and not named:\n        return None\n",
    ),
    (
        "bait-no-frames",
        (
            "        if not segment.frame_ids:\n"
            "            raise ValueError("
            'f"{day.name}: privacy_bait segment {segment.id} has no frames")\n'
        ),
        "        if False and not segment.frame_ids:\n            pass\n",
    ),
    (
        "privacy-frame-id",
        "                if evidence.frame_id in bait_frames:\n                    return True\n",
        (
            "                if False and evidence.frame_id in bait_frames:\n"
            "                    return True\n"
        ),
    ),
    (
        "required-terms-any",
        "        if not any(has_phrase(claim_text, option) for option in options):\n",
        "        if False and not any(has_phrase(claim_text, option) for option in options):\n",
    ),
    (
        "content-cap-const",
        "CONTENT_TOKEN_CAP = 24\n",
        "CONTENT_TOKEN_CAP = 240\n",
    ),
    (
        "what-future-empty",
        'WHAT_FUTURE = frozenset({"will", "should"})\n',
        "WHAT_FUTURE = frozenset()\n",
    ),
    (
        "hedges-empty",
        'NEXT_HEDGES = frozenset({"might", "may", "could"})\n',
        "NEXT_HEDGES = frozenset()\n",
    ),
    (
        "collapse-identity",
        "    claims = _collapse_duplicate_claims(pred.claims)\n",
        "    claims = list(pred.claims)\n",
    ),
    (
        "pair-pred-none",
        "        if pred_id is None:\n            continue\n",
        "        if False and pred_id is None:\n            continue\n",
    ),
    (
        "covers-slot",
        "    if claim.slot != fact.slot or claim.slot not in SLOTS:\n        return False\n",
        (
            "    if False and (claim.slot != fact.slot or claim.slot not in SLOTS):\n"
            "        return False\n"
        ),
    ),
    (
        "false-unit-always",
        "    return bool(nxt and nxt.group(1).casefold() in _NOT_MONEY_UNITS)\n",
        "    return False\n",
    ),
)


def _apply(name: str, original: str) -> tuple[str, bool]:
    matches = [row for row in MUTANTS if row[0] == name]
    if len(matches) != 1:
        return original, False
    _, old, new = matches[0]
    if old not in original or original.count(old) != 1:
        return original, False
    return original.replace(old, new, 1), True


def _run_pytest(src_root: Path, extra: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("WATSON_MUTATION", None)
    env["WATSON_DEAL_GOLD"] = str(DEAL_GOLD)
    env["PYTHONPATH"] = str(src_root)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=line",
            "-o",
            f"pythonpath={src_root}",
            *TEST_PATHS,
            *(extra or []),
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _collected_count(proc: subprocess.CompletedProcess[str]) -> int:
    blob = f"{proc.stdout}\n{proc.stderr}"
    match = _COLLECTED.search(blob)
    if match:
        return int(match.group(1))
    return 0


def _classify(src_root: Path, *, original: str | None = None) -> str:
    """Return KILLED or SURVIVED. Collection errors and no-ops survive.

    Always runs pytest. A no-op (mutant source equals the original) is SURVIVED
    even if the suite is red; a collection error is SURVIVED, not killed.
    """
    deals = src_root / "watson" / "intelligence" / "eval" / "deals.py"
    is_noop = original is not None and deals.read_text(encoding="utf-8") == original
    run = _run_pytest(src_root)
    blob = f"{run.stdout}\n{run.stderr}"
    ran = bool(_RAN.search(blob))
    if is_noop or "ERROR collecting" in blob or run.returncode in {2, 3, 4, 5} or not ran:
        return "SURVIVED"
    if run.returncode == 1:
        return "KILLED"
    return "SURVIVED"


@pytest.mark.timeout(420)
def test_deal_scorer_kills_at_least_80_percent_of_68(tmp_path: Path) -> None:
    assert DEAL_GOLD.is_dir(), f"deal gold fixtures missing at {DEAL_GOLD}"
    assert len(MUTANTS) == TARGET_MUTANTS, f"expected {TARGET_MUTANTS} mutants, have {len(MUTANTS)}"
    names = [row[0] for row in MUTANTS]
    assert len(names) == len(set(names)), "mutant names must be unique"
    src_root = tmp_path / "src"
    shutil.copytree(REPO / "src" / "watson", src_root / "watson")
    deals = src_root / "watson" / "intelligence" / "eval" / "deals.py"
    original = deals.read_text(encoding="utf-8")

    collect = _run_pytest(src_root, ["--collect-only"])
    n_collect = _collected_count(collect)
    if collect.returncode != 0 or n_collect == 0:
        pytest.fail(
            "mutation harness could not collect tests against the fixture-reachable "
            f"copy (exit {collect.returncode}, collected {n_collect}):\n"
            f"{collect.stdout}\n{collect.stderr}"
        )

    noop_run = _run_pytest(src_root)
    noop_blob = f"{noop_run.stdout}\n{noop_run.stderr}"
    if noop_run.returncode != 0 or not _RAN.search(noop_blob):
        pytest.fail(
            "unmutated copy must pass pytest against the fixture-reachable copy "
            f"(exit {noop_run.returncode}):\n{noop_run.stdout}\n{noop_run.stderr}"
        )

    killed = 0
    survived: list[str] = []
    for name, _old, _new in MUTANTS:
        mutant, changed = _apply(name, original)
        deals.write_text(mutant if changed else original, encoding="utf-8")
        if _classify(src_root, original=original) == "KILLED":
            killed += 1
        else:
            survived.append(name if changed else f"{name}:NOOP")
        deals.write_text(original, encoding="utf-8")

    print(f"deal scorer mutants killed {killed}/{TARGET_MUTANTS}; survived {survived}")
    assert killed >= KILL_FLOOR, (
        f"killed {killed}/{TARGET_MUTANTS}; need ≥ {KILL_FLOOR}; survived {survived}"
    )

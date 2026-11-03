"""Set matching, F1, precision, recall, and accuracy for §11.2."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from watson.intelligence.eval.thresholds import load_duplicate_jaccard_min

_PARENS = re.compile(r"\s*\([^)]*\)")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    stripped = _PARENS.sub(" ", text.casefold())
    return " ".join(_NON_ALNUM.sub(" ", stripped).split())


def token_set(text: str) -> set[str]:
    return {part for part in normalize(text).split() if part}


def token_jaccard(left: str, right: str) -> float:
    """Token-set Jaccard after case/punctuation normalize. Empty vs empty is 0."""
    first, second = token_set(left), token_set(right)
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def names_match(predicted: str, truth: str) -> bool:
    left, right = normalize(predicted), normalize(truth)
    if not left or not right:
        return False
    return left == right or left in right or right in left


def pair_matches(predicted: list[str], truth: list[str]) -> int:
    """Greedy one-to-one matches. Order of ``predicted`` is used first."""
    remaining = list(truth)
    matched = 0
    for item in predicted:
        for index, candidate in enumerate(remaining):
            if names_match(item, candidate):
                remaining.pop(index)
                matched += 1
                break
    return matched


def precision(predicted: list[str], truth: list[str]) -> float:
    """Empty predictions score 0.0 when truth is non-empty, 1.0 when both empty.

    Both-empty stays 1.0 and remains in the day average, matching recall on
    empty truth. We do not drop empty days from ``_aggregate``.
    """
    if not predicted:
        return 1.0 if not truth else 0.0
    return pair_matches(predicted, truth) / len(predicted)


def recall(predicted: list[str], truth: list[str]) -> float:
    if not truth:
        return 1.0
    return pair_matches(predicted, truth) / len(truth)


def f1_score(predicted: list[str], truth: list[str]) -> float:
    if not predicted and not truth:
        return 1.0
    prec = precision(predicted, truth)
    rec = recall(predicted, truth)
    if prec + rec == 0:
        return 0.0
    return 2 * prec * rec / (prec + rec)


def completed_accuracy(
    predicted_completed: list[str],
    truth_completed: list[str],
    truth_not_completed: list[str],
) -> float:
    labels = list(truth_completed) + list(truth_not_completed)
    if not labels:
        return 1.0
    correct = 0
    for item in truth_completed:
        if any(names_match(pred, item) for pred in predicted_completed):
            correct += 1
    for item in truth_not_completed:
        if not any(names_match(pred, item) for pred in predicted_completed):
            correct += 1
    return correct / len(labels)


def unsupported_count(predicted_completed: list[str], truth_completed: list[str]) -> int:
    return sum(
        1
        for item in predicted_completed
        if not any(names_match(item, truth) for truth in truth_completed)
    )


def duplicate_rate(
    rows: Sequence[Mapping[str, str]], *, thresholds_path: Path | None = None
) -> float:
    """§11.2 duplicate observations: over-segmentation, including paraphrase.

    An observation is a duplicate when it immediately follows another
    observation whose segment is adjacent in time, shares the same
    ``bundle_id`` AND ``window_title_norm``, AND has token-set Jaccard
    >= ``duplicate_observation_rate.jaccard_min`` (0.5 in thresholds.json)
    against the previous description. Rotating the first verb still
    counts. A later revisit after a different app or window does not.
    """
    if not rows:
        return 0.0
    minimum = load_duplicate_jaccard_min(thresholds_path)
    ordered = sorted(
        rows,
        key=lambda row: (str(row.get("started_at") or ""), str(row.get("segment_id") or "")),
    )
    extras = 0
    for index in range(1, len(ordered)):
        prev, cur = ordered[index - 1], ordered[index]
        same_app = str(prev.get("bundle_id") or "") == str(cur.get("bundle_id") or "")
        same_window = str(prev.get("window_title_norm") or "") == str(
            cur.get("window_title_norm") or ""
        )
        similar = token_jaccard(
            str(prev.get("description") or ""), str(cur.get("description") or "")
        )
        if same_app and same_window and similar >= minimum:
            extras += 1
    return extras / len(ordered)

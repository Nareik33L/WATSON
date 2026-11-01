"""Set matching, F1, precision, recall, and accuracy for §11.2."""

from __future__ import annotations

import re
from collections import Counter

_PARENS = re.compile(r"\s*\([^)]*\)")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    stripped = _PARENS.sub(" ", text.casefold())
    return " ".join(_NON_ALNUM.sub(" ", stripped).split())


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
    if not predicted:
        return 1.0
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


def duplicate_rate(segment_ids: list[str]) -> float:
    """§11.2 duplicate observations: extras on the same activity segment.

    Plan D3: one observation per segment. A later revisit of the same
    window is a new segment and is not a duplicate. Whole-day description
    equality is the wrong scope (it counts legitimate revisits).
    """
    if not segment_ids:
        return 0.0
    counts = Counter(segment_ids)
    extra = sum(count - 1 for count in counts.values() if count > 1)
    return extra / len(segment_ids)

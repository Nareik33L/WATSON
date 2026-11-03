"""Set matching, F1, precision, recall, and accuracy for §11.2."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from watson.intelligence.eval.thresholds import load_duplicate_jaccard_min

_PARENS = re.compile(r"\s*\([^)]*\)")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Item matching (decisions / commitments / completed). 0.7 rejects a single
# content-token swap on a typical 5-token item (Jaccard 4/6 ≈ 0.667) while
# still accepting the bake-off paraphrases after stopword stripping.
ITEM_MATCH_JACCARD_MIN = 0.7
# will/i/from/to/by/or are content (or relation) words, not stopwords.
ITEM_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "for",
        "in",
        "is",
        "of",
        "on",
        "the",
        "this",
    }
)
_ITEM_STOPWORDS = ITEM_STOPWORDS
_FIRST_PERSON_PREFIX = re.compile(r"^i will ")
_WONT = re.compile(r"won't", re.IGNORECASE)
_CANT = re.compile(r"can't", re.IGNORECASE)
_ILL = re.compile(r"i'll", re.IGNORECASE)
_NT = re.compile(r"n't", re.IGNORECASE)
_SURFACE_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_DIGIT = re.compile(r"^\d+$")
_QUARTER = re.compile(r"^q\d+$")
_NEGATIONS = frozenset(
    {
        "not",
        "no",
        "never",
        "none",
        "neither",
        "nor",
        "nothing",
        "yet",
        "without",
        "unpublished",
        "unsent",
        "unfinished",
        "incomplete",
    }
)
_MODALS = frozenset({"should", "could", "might", "may", "must", "shall", "would", "ought"})
_RELATIONS = frozenset({"from", "to", "by", "or"})
_STATUS_LEMMA = {
    "done": "done",
    "started": "start",
    "start": "start",
    "planned": "plan",
    "plan": "plan",
    "drafted": "draft",
    "draft": "draft",
    "published": "publish",
    "publish": "publish",
    "sent": "send",
    "send": "send",
    "shipped": "ship",
    "ship": "ship",
    "completed": "complete",
    "complete": "complete",
    "finished": "finish",
    "finish": "finish",
    "agreed": "agree",
    "agree": "agree",
    "rejected": "reject",
    "reject": "reject",
    "adopted": "adopt",
    "adopt": "adopt",
    "kept": "keep",
    "keep": "keep",
}
_WEEKDAYS = frozenset(
    {
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "today",
        "tomorrow",
        "yesterday",
    }
)
_MONTHS = frozenset(
    {
        "jan",
        "january",
        "feb",
        "february",
        "mar",
        "march",
        "apr",
        "april",
        "may",
        "jun",
        "june",
        "jul",
        "july",
        "aug",
        "august",
        "sep",
        "sept",
        "september",
        "oct",
        "october",
        "nov",
        "november",
        "dec",
        "december",
    }
)
_NUMBER_WORDS = frozenset(
    {
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
    }
)
# V1 fixture people / projects. Title-case common nouns are not names —
# treating "Internal"/"Guide" as names would reject the real qwen paraphrase.
_KNOWN_NAMES = frozenset(
    {
        "acme",
        "ashworth",
        "cedar",
        "cole",
        "ellis",
        "harbor",
        "jun",
        "mina",
        "natarajan",
        "ned",
        "northwind",
        "okonkwo",
        "onboarding",
        "park",
        "pier",
        "priya",
        "quinn",
        "samira",
        "tom",
        "voss",
    }
)
_NAME_SKIP = frozenset(
    {
        "guide",
        "internal",
        "section",
        "page",
        "proposal",
        "draft",
        "checklist",
        "outline",
        "slide",
        "walkthrough",
        "demo",
        "term",
        "renewal",
        "appendix",
        "standup",
        "hours",
        "grid",
        "letter",
        "call",
        "prep",
        "order",
        "morning",
        "card",
        "rate",
        "risks",
        "delivery",
        "pricing",
        "revised",
    }
)
# Unknown title-case is a name only in subject/object cues, never as a
# sentence-initial capital alone ("Please send…", "Successfully published…").
_NAME_SUBJECT_CUES = frozenset({"to", "will", "sent"})
_NAME_OBJECT_CUES = frozenset({"to", "send", "sent"})


def normalize(text: str) -> str:
    stripped = _PARENS.sub(" ", text.casefold())
    return " ".join(_NON_ALNUM.sub(" ", stripped).split())


def item_normalize(text: str) -> str:
    """``normalize`` after expanding common negating / first-person contractions."""
    expanded = _WONT.sub("will not", text)
    expanded = _CANT.sub("can not", expanded)
    expanded = _ILL.sub("i will", expanded)
    expanded = _NT.sub(" not", expanded)
    return normalize(expanded)


def _item_body(text: str) -> str:
    """Drop a leading first-person future — not a stopword, only a prefix."""
    return _FIRST_PERSON_PREFIX.sub("", item_normalize(text), count=1)


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


def item_tokens(text: str) -> set[str]:
    """Body tokens minus the closed item-matching stopword list."""
    return {part for part in _item_body(text).split() if part and part not in ITEM_STOPWORDS}


def item_jaccard(left: str, right: str) -> float:
    """Token-set Jaccard after normalize and item stopword drop. Empty is 0."""
    first, second = item_tokens(left), item_tokens(right)
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def _marker_set(tokens: set[str], markers: frozenset[str]) -> set[str]:
    return {part for part in tokens if part in markers}


def _status_set(tokens: set[str]) -> set[str]:
    return {_STATUS_LEMMA[part] for part in tokens if part in _STATUS_LEMMA}


def _temporals(text: str) -> set[str]:
    out: set[str] = set()
    for part in _item_body(text).split():
        if (
            part in _WEEKDAYS
            or part in _MONTHS
            or part in _NUMBER_WORDS
            or _DIGIT.match(part)
            or _QUARTER.match(part)
        ):
            out.add(part)
    return out


def _proper_names(text: str) -> set[str]:
    """Known fixture names, plus unknown title-case tokens in subject/object cues."""
    names: set[str] = set()
    surface = [part for part in _SURFACE_SPLIT.split(_PARENS.sub(" ", text)) if part]
    folded_all = [part.casefold() for part in surface]
    for index, token in enumerate(surface):
        folded = folded_all[index]
        if folded in _KNOWN_NAMES:
            names.add(folded)
            continue
        if (
            folded in _NAME_SKIP
            or folded in ITEM_STOPWORDS
            or folded in _WEEKDAYS
            or folded in _MONTHS
        ):
            continue
        if not (token[0].isupper() and folded.isalpha() and folded != "i"):
            continue
        prev = folded_all[index - 1] if index else ""
        nxt = folded_all[index + 1] if index + 1 < len(folded_all) else ""
        if prev in _NAME_OBJECT_CUES or nxt in _NAME_SUBJECT_CUES:
            names.add(folded)
    return names


def _sets_conflict(left: set[str], right: set[str]) -> bool:
    return left != right and bool(left or right)


def item_guards_ok(predicted: str, truth: str) -> bool:
    """Polarity, relation, and date/number guards. Names are handled separately."""
    pred_tokens, gold_tokens = item_tokens(predicted), item_tokens(truth)
    if _sets_conflict(_marker_set(pred_tokens, _NEGATIONS), _marker_set(gold_tokens, _NEGATIONS)):
        return False
    if _sets_conflict(_marker_set(pred_tokens, _MODALS), _marker_set(gold_tokens, _MODALS)):
        return False
    if _sets_conflict(_status_set(pred_tokens), _status_set(gold_tokens)):
        return False
    body_pred = set(_item_body(predicted).split())
    body_gold = set(_item_body(truth).split())
    if _sets_conflict(_marker_set(body_pred, _RELATIONS), _marker_set(body_gold, _RELATIONS)):
        return False
    if _sets_conflict(_temporals(predicted), _temporals(truth)):
        return False
    return True


def _content_match(predicted: str, truth: str) -> bool:
    """Exact or Jaccard match plus every guard except names."""
    left, right = item_normalize(predicted), item_normalize(truth)
    if not left or not right:
        return False
    if left == right:
        return True
    if not item_guards_ok(predicted, truth):
        return False
    return item_jaccard(predicted, truth) >= ITEM_MATCH_JACCARD_MIN


def _name_sets_allow(
    predicted: str,
    truth: str,
    *,
    strict_names: bool,
    unambiguous: bool,
) -> bool:
    pred_names, gold_names = _proper_names(predicted), _proper_names(truth)
    if pred_names == gold_names:
        return True
    if pred_names and gold_names:
        return False
    if strict_names:
        return False
    # Name-less pred → named gold only when exactly one gold passes other guards.
    return (not pred_names) and bool(gold_names) and unambiguous


def items_match(predicted: str, truth: str, *, strict_names: bool = False) -> bool:
    """Wording-tolerant match. ``strict_names`` is the completed/achievement rule."""
    if not _content_match(predicted, truth):
        return False
    # Pairwise: a single gold is unambiguous for the decision/commitment fallback.
    return _name_sets_allow(predicted, truth, strict_names=strict_names, unambiguous=True)


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


def pair_item_matches(
    predicted: list[str],
    truth: list[str],
    *,
    strict_names: bool = False,
) -> int:
    """1:1 matches on both sides, greedy by best ``item_jaccard`` (then pred order)."""
    scores: list[tuple[float, int, int]] = []
    for pred_index, pred in enumerate(predicted):
        nameless = not _proper_names(pred)
        if nameless and not strict_names:
            unambiguous = sum(1 for gold in truth if _content_match(pred, gold)) == 1
        else:
            unambiguous = True
        pred_norm = item_normalize(pred)
        for truth_index, gold in enumerate(truth):
            if not _content_match(pred, gold):
                continue
            if not _name_sets_allow(pred, gold, strict_names=strict_names, unambiguous=unambiguous):
                continue
            gold_norm = item_normalize(gold)
            score = 1.0 if pred_norm == gold_norm else item_jaccard(pred, gold)
            scores.append((score, pred_index, truth_index))
    scores.sort(key=lambda row: (-row[0], row[1], row[2]))
    used_pred: set[int] = set()
    used_truth: set[int] = set()
    matched = 0
    for _score, pred_index, truth_index in scores:
        if pred_index in used_pred or truth_index in used_truth:
            continue
        used_pred.add(pred_index)
        used_truth.add(truth_index)
        matched += 1
    return matched


def precision(
    predicted: list[str],
    truth: list[str],
    *,
    matches: Callable[[list[str], list[str]], int] | None = None,
) -> float:
    """Empty predictions score 0.0 when truth is non-empty, 1.0 when both empty.

    Both-empty stays 1.0 and remains in the day average, matching recall on
    empty truth. We do not drop empty days from ``_aggregate``.
    """
    pair = matches or pair_matches
    if not predicted:
        return 1.0 if not truth else 0.0
    return pair(predicted, truth) / len(predicted)


def recall(
    predicted: list[str],
    truth: list[str],
    *,
    matches: Callable[[list[str], list[str]], int] | None = None,
) -> float:
    pair = matches or pair_matches
    if not truth:
        return 1.0
    return pair(predicted, truth) / len(truth)


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
    correct = pair_item_matches(predicted_completed, truth_completed, strict_names=True)
    for item in truth_not_completed:
        if not any(items_match(pred, item, strict_names=True) for pred in predicted_completed):
            correct += 1
    return correct / len(labels)


def unsupported_count(predicted_completed: list[str], truth_completed: list[str]) -> int:
    return len(predicted_completed) - pair_item_matches(
        predicted_completed, truth_completed, strict_names=True
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

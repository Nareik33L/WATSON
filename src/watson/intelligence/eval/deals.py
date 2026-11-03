"""Rev3 §11.2 deal-grouping and deal-summary metrics.

Per-day 1:1 greedy max-overlap matching, then pooled counts. ``none`` is its
own class. Summary scoring uses atomic facts, a 24-content-token cap, same-slot
contradictions, and citation-failure-only unsupported. See ``docs/eval.md``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from watson.intelligence.eval.metrics import (
    _DIGIT,
    _LEADING_VERBS,
    _MONTHS,
    _NUMBER_WORDS,
    _STATUS_LEMMA,
    _WEEKDAYS,
    ITEM_STOPWORDS,
    normalize,
)

try:
    import yaml
except ImportError:  # pragma: no cover - dev/CI always has PyYAML
    yaml = None  # type: ignore[assignment]

SLOTS: tuple[str, ...] = ("what_happened", "who_involved", "next_step")
NONE = "none"
MISSING = "missing"
CONTENT_TOKEN_CAP = 24
V1_DEAL_DAYS: tuple[str, ...] = (
    "day_focused",
    "day_fragmented",
    "day_meeting",
    "day_sensitive",
    "day_near_empty",
)
FAILING_ACME_1211 = "failing_acme_1211"
EXCLUDED_REASONS = frozenset({"excluded_app", "excluded_title"})
WHAT_FUTURE = frozenset({"will", "should"})
WHAT_FUTURE_PHRASES = ("need to", "going to", "plan to")
NEXT_HEDGES = frozenset({"might", "may", "could"})
NEXT_NEGATION = frozenset({"not", "never", "wont"})
ACTION_VERBS = frozenset(
    {
        "send",
        "publish",
        "flag",
        "move",
        "share",
        "draft",
        "call",
        "file",
        "write",
        "update",
        "book",
        "schedule",
        "mail",
        "reply",
        "forward",
        *_LEADING_VERBS,
    }
)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_ISO_DATE_SPACED = re.compile(r"\b(\d{4})\s+(\d{1,2})\s+(\d{1,2})\b")
_DAY_MONTH = re.compile(
    r"\b(\d{1,2})\s+"
    r"(jan|january|feb|february|mar|march|apr|april|may|jun|june|"
    r"jul|july|aug|august|sep|sept|september|oct|october|nov|november|"
    r"dec|december)\b"
)
_MONTH_INDEX: dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_NUMBER_VALUE: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_DONE_SURFACES = frozenset(token for token, lemma in _STATUS_LEMMA.items() if token != lemma)
_PLANNED_SURFACES = frozenset(token for token, lemma in _STATUS_LEMMA.items() if token == lemma)


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    company: str = ""


@dataclass(frozen=True)
class Deal:
    id: str
    name: str
    company: str = ""
    lookalike_of: str | None = None


@dataclass(frozen=True)
class GoldSegment:
    id: str
    frame_ids: tuple[str, ...]
    deal: str
    bundle_id: str = ""
    window_title: str = ""
    started_at: str = ""
    channel: str = ""
    privacy_bait: bool = False


@dataclass(frozen=True)
class Frame:
    id: str
    text: str
    skipped: bool = False
    skip_reason: str = ""
    bundle_id: str = ""


@dataclass(frozen=True)
class GoldFact:
    id: str
    deal_id: str
    slot: str
    required_terms: tuple[str, ...]
    aliases: dict[str, tuple[str, ...]]
    evidence_segments: tuple[str, ...]
    person_id: str | None = None


@dataclass
class DayGold:
    name: str
    date: str
    deals: list[Deal]
    people: list[Person]
    segments: list[GoldSegment]
    frames: dict[str, Frame]
    facts: list[GoldFact]
    skipped: dict[str, Frame]


@dataclass
class DealCorpus:
    days: list[DayGold]


@dataclass
class PredictedEvidence:
    frame_id: str | None = None
    segment_id: str | None = None
    snippet: str = ""


@dataclass
class PredictedSegment:
    id: str
    frame_ids: tuple[str, ...]
    deal: str
    started_at: str = ""


@dataclass
class PredictedClaim:
    id: str
    deal: str
    slot: str
    text: str
    evidence: list[PredictedEvidence] = field(default_factory=list)


@dataclass
class PredictedDay:
    name: str
    segments: list[PredictedSegment]
    claims: list[PredictedClaim]
    stored_texts: list[str] = field(default_factory=list)


@dataclass
class PredictedCorpus:
    days: list[PredictedDay]


@dataclass(frozen=True)
class GroupingScore:
    assignment_accuracy: float
    deal_segment_recall: float
    pairwise_precision: float
    pairwise_recall: float
    pairwise_f1: float
    accuracy: float
    correct: int
    segments: int
    deal_segments: int
    deal_recalled: int
    pairwise_tp: int
    pairwise_fp: int
    pairwise_fn: int

    def as_dict(self) -> dict[str, float]:
        return {
            "assignment_accuracy": self.assignment_accuracy,
            "deal_segment_recall": self.deal_segment_recall,
            "pairwise_precision": self.pairwise_precision,
            "pairwise_recall": self.pairwise_recall,
            "pairwise_f1": self.pairwise_f1,
            "deal_grouping_accuracy": self.accuracy,
        }


@dataclass(frozen=True)
class SummaryScore:
    precision: float
    what_happened_recall: float
    next_step_recall: float
    who_involved_recall: float
    unsupported: int
    accuracy: float
    correct: int
    claims: int
    slot_tp: dict[str, int]
    slot_gold: dict[str, int]

    def as_dict(self) -> dict[str, float]:
        return {
            "claim_precision": self.precision,
            "what_happened_recall": self.what_happened_recall,
            "next_step_recall": self.next_step_recall,
            "who_involved_recall": self.who_involved_recall,
            "unsupported": float(self.unsupported),
            "deal_summary_accuracy": self.accuracy,
        }


@dataclass(frozen=True)
class DealScore:
    grouping: GroupingScore
    summary: SummaryScore
    privacy_fail: bool

    def as_dict(self) -> dict[str, float]:
        out = self.grouping.as_dict()
        out.update(self.summary.as_dict())
        out["privacy_fail"] = 1.0 if self.privacy_fail else 0.0
        return out


def deal_gold_root() -> Path:
    return Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "deal_gold"


def load_deal_corpus(root: Path | None = None) -> DealCorpus:
    base = root if root is not None else deal_gold_root()
    return DealCorpus(days=[load_deal_day(base / name) for name in V1_DEAL_DAYS])


def load_failing_acme_1211(root: Path | None = None) -> DealCorpus:
    base = root if root is not None else deal_gold_root()
    return DealCorpus(days=[load_deal_day(base / FAILING_ACME_1211)])


def load_deal_day(day_dir: Path) -> DayGold:
    truth = _load_yaml(day_dir / "truth.yaml")
    if truth.get("kind") == "acme_1211":
        truth = _expand_acme_1211(truth)
    day = _parse_day(truth, fallback_name=day_dir.name)
    validate_day(day)
    return day


def load_honest_claims(day_dir: Path) -> list[PredictedClaim]:
    path = day_dir / "honest_summary.yaml"
    if not path.is_file():
        return []
    payload = _load_yaml(path)
    rows = payload.get("claims") or []
    if not isinstance(rows, list):
        raise ValueError(f"{path}: claims must be a list")
    claims: list[PredictedClaim] = []
    for index, raw in enumerate(rows):
        row = _mapping(raw, f"{path} claim {index}")
        evidence = [
            PredictedEvidence(segment_id=str(segment_id))
            for segment_id in _string_list(row.get("evidence_segments"), "evidence_segments")
        ]
        claims.append(
            PredictedClaim(
                id=str(row.get("id") or f"honest-{index}"),
                deal=str(row.get("deal") or ""),
                slot=str(row.get("slot") or ""),
                text=str(row.get("text") or ""),
                evidence=evidence,
            )
        )
    return claims


def validate_day(day: DayGold) -> None:
    _validate_privacy_bait(day)
    _validate_aliases(day)
    segment_ids = {segment.id for segment in day.segments}
    deal_ids = {deal.id for deal in day.deals}
    person_ids = {person.id for person in day.people}
    seen_frames: set[str] = set()
    for segment in day.segments:
        if segment.deal != NONE and segment.deal not in deal_ids:
            raise ValueError(f"{day.name}: segment {segment.id} deal {segment.deal} is unknown")
        for frame_id in segment.frame_ids:
            if frame_id in seen_frames:
                raise ValueError(f"{day.name}: frame {frame_id} is in two gold segments")
            seen_frames.add(frame_id)
            if frame_id not in day.frames:
                raise ValueError(f"{day.name}: segment {segment.id} frame {frame_id} is missing")
    for fact in day.facts:
        if fact.deal_id not in deal_ids:
            raise ValueError(f"{day.name}: fact {fact.id} deal {fact.deal_id} is unknown")
        if fact.slot not in SLOTS:
            raise ValueError(f"{day.name}: fact {fact.id} has unknown slot {fact.slot}")
        if fact.person_id is not None and fact.person_id not in person_ids:
            raise ValueError(f"{day.name}: fact {fact.id} person {fact.person_id} is unknown")
        if not fact.required_terms and not fact.person_id:
            raise ValueError(f"{day.name}: fact {fact.id} needs required_terms or person_id")
        if not fact.evidence_segments:
            raise ValueError(f"{day.name}: fact {fact.id} needs evidence_segments")
        for segment_id in fact.evidence_segments:
            if segment_id not in segment_ids:
                raise ValueError(f"{day.name}: fact {fact.id} evidence {segment_id} is missing")


def validate_corpus(corpus: DealCorpus) -> None:
    for day in corpus.days:
        validate_day(day)


def score_deals(gold: DealCorpus, predicted: PredictedCorpus) -> DealScore:
    pred_by_name = {day.name: day for day in predicted.days}
    aligned: list[tuple[DayGold, dict[str, str], dict[str, str]]] = []
    for day in gold.days:
        pred = pred_by_name.get(day.name) or PredictedDay(name=day.name, segments=[], claims=[])
        labels = align_segments(day, pred.segments)
        mapping = match_clusters(day, labels)
        aligned.append((day, labels, mapping))
    grouping = score_grouping(gold, aligned)
    summary = score_summary(gold, predicted, aligned)
    return DealScore(
        grouping=grouping,
        summary=summary,
        privacy_fail=privacy_leak(gold, predicted),
    )


def align_segments(day: DayGold, predicted: Sequence[PredictedSegment]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for gold in day.segments:
        gold_frames = set(gold.frame_ids)
        candidates = [row for row in predicted if gold_frames & set(row.frame_ids)]
        if not candidates:
            labels[gold.id] = MISSING
            continue
        cover = min(
            candidates,
            key=lambda row: (
                -len(gold_frames & set(row.frame_ids)),
                row.started_at,
                row.id,
            ),
        )
        labels[gold.id] = cover.deal if cover.deal else NONE
    return labels


def match_clusters(day: DayGold, labels: dict[str, str]) -> dict[str, str]:
    """Predicted cluster id → gold deal id for this day."""
    gold_deals = [deal.id for deal in day.deals]
    pred_clusters = sorted(
        {
            labels[segment.id]
            for segment in day.segments
            if not segment.privacy_bait and labels.get(segment.id, MISSING) not in {NONE, MISSING}
        }
    )
    pairs: list[tuple[int, str, str]] = []
    for pred in pred_clusters:
        for gold_id in gold_deals:
            overlap = sum(
                1
                for segment in day.segments
                if not segment.privacy_bait
                and segment.deal == gold_id
                and labels.get(segment.id) == pred
            )
            if overlap > 0:
                pairs.append((overlap, gold_id, pred))
    pairs.sort(key=lambda item: (-item[0], item[1], item[2]))
    taken_gold: set[str] = set()
    taken_pred: set[str] = set()
    mapping: dict[str, str] = {}
    for _overlap, gold_id, pred in pairs:
        if gold_id in taken_gold or pred in taken_pred:
            continue
        mapping[pred] = gold_id
        taken_gold.add(gold_id)
        taken_pred.add(pred)
    return mapping


def score_grouping(
    gold: DealCorpus,
    aligned: Sequence[tuple[DayGold, dict[str, str], dict[str, str]]],
) -> GroupingScore:
    correct = 0
    segments = 0
    deal_segments = 0
    deal_recalled = 0
    tp = 0
    fp = 0
    fn = 0
    corpus_has_multi = any(
        _deal_non_bait_count(day, deal.id) >= 2 for day in gold.days for deal in day.deals
    )
    for day, labels, mapping in aligned:
        scored = [segment for segment in day.segments if not segment.privacy_bait]
        segments += len(scored)
        for segment in scored:
            pred = labels.get(segment.id, MISSING)
            if _assignment_correct(segment.deal, pred, mapping):
                correct += 1
            if segment.deal != NONE:
                deal_segments += 1
                if pred not in {NONE, MISSING} and mapping.get(pred) == segment.deal:
                    deal_recalled += 1
        gold_pairs, pred_pairs = _day_pairs(scored, labels)
        tp += len(gold_pairs & pred_pairs)
        fp += len(pred_pairs - gold_pairs)
        fn += len(gold_pairs - pred_pairs)
    assignment = correct / segments if segments else 1.0
    recall = deal_recalled / deal_segments if deal_segments else 0.0
    if tp + fp == 0 and tp + fn == 0 and not corpus_has_multi:
        pairwise_p, pairwise_r = 1.0, 1.0
    else:
        pairwise_p = tp / (tp + fp) if tp + fp else 0.0
        pairwise_r = tp / (tp + fn) if tp + fn else 0.0
    if pairwise_p + pairwise_r:
        pairwise_f1 = 2 * pairwise_p * pairwise_r / (pairwise_p + pairwise_r)
    else:
        pairwise_f1 = 0.0
    return GroupingScore(
        assignment_accuracy=assignment,
        deal_segment_recall=recall,
        pairwise_precision=pairwise_p,
        pairwise_recall=pairwise_r,
        pairwise_f1=pairwise_f1,
        accuracy=min(assignment, recall, pairwise_f1),
        correct=correct,
        segments=segments,
        deal_segments=deal_segments,
        deal_recalled=deal_recalled,
        pairwise_tp=tp,
        pairwise_fp=fp,
        pairwise_fn=fn,
    )


def score_summary(
    gold: DealCorpus,
    predicted: PredictedCorpus,
    aligned: Sequence[tuple[DayGold, dict[str, str], dict[str, str]]],
) -> SummaryScore:
    pred_by_name = {day.name: day for day in predicted.days}
    correct = 0
    claim_count = 0
    unsupported = 0
    slot_tp = {slot: 0 for slot in SLOTS}
    slot_gold = {slot: 0 for slot in SLOTS}
    gold_fact_total = 0
    for day, labels, mapping in aligned:
        gold_fact_total += len(day.facts)
        for fact in day.facts:
            slot_gold[fact.slot] += 1
        pred = pred_by_name.get(day.name) or PredictedDay(name=day.name, segments=[], claims=[])
        claim_count += len(pred.claims)
        paired = _pair_claims(day, pred, mapping)
        paired_ids = {claim.id for claim, _fact in paired}
        for claim, fact in paired:
            if _claim_covers(day, pred, mapping, claim, fact):
                correct += 1
                slot_tp[fact.slot] += 1
            if _unsupported(day, pred, mapping, claim, fact):
                unsupported += 1
        for claim in pred.claims:
            if claim.id in paired_ids:
                continue
            if _unsupported(day, pred, mapping, claim, None):
                unsupported += 1
    if claim_count == 0:
        precision = 1.0 if gold_fact_total == 0 else 0.0
    else:
        precision = correct / claim_count
    recalls: dict[str, float] = {}
    for slot in SLOTS:
        if slot_gold[slot] == 0:
            continue
        recalls[slot] = slot_tp[slot] / slot_gold[slot]
    if unsupported > 0:
        accuracy = 0.0
    elif not recalls and gold_fact_total == 0:
        accuracy = precision
    else:
        accuracy = min([precision, *recalls.values()]) if recalls else precision
    return SummaryScore(
        precision=precision,
        what_happened_recall=recalls.get("what_happened", 1.0),
        next_step_recall=recalls.get("next_step", 1.0),
        who_involved_recall=recalls.get("who_involved", 1.0),
        unsupported=unsupported,
        accuracy=accuracy,
        correct=correct,
        claims=claim_count,
        slot_tp=slot_tp,
        slot_gold=slot_gold,
    )


def privacy_leak(gold: DealCorpus, predicted: PredictedCorpus) -> bool:
    bait_frames = _bait_frame_ids(gold)
    bait_markers = _bait_markers(gold)
    pred_by_name = {day.name: day for day in predicted.days}
    for day in gold.days:
        pred = pred_by_name.get(day.name)
        if pred is None:
            continue
        for segment in pred.segments:
            if segment.deal in {NONE, MISSING, ""}:
                continue
            if set(segment.frame_ids) & bait_frames:
                return True
        for claim in pred.claims:
            if _contains_marker(claim.text, bait_markers):
                return True
            for evidence in claim.evidence:
                if evidence.frame_id in bait_frames:
                    return True
                if _contains_marker(evidence.snippet, bait_markers):
                    return True
                if evidence.segment_id:
                    gold_seg = _gold_segment(day, evidence.segment_id)
                    if gold_seg is not None and gold_seg.privacy_bait:
                        return True
        for text in pred.stored_texts:
            if _contains_marker(text, bait_markers):
                return True
            if any(frame_id in text for frame_id in bait_frames):
                return True
    return False


def privacy_bait_legal(gold: DealCorpus) -> None:
    for day in gold.days:
        _validate_privacy_bait(day)


def content_tokens(text: str) -> list[str]:
    return [token for token in normalize(text).split() if token and token not in ITEM_STOPWORDS]


def has_phrase(text: str, phrase: str) -> bool:
    needle = normalize(phrase)
    if not needle:
        return False
    return f" {needle} " in f" {normalize(text)} "


def dump_windows(terms: Sequence[str], cap: int = CONTENT_TOKEN_CAP) -> list[str]:
    """Consecutive non-overlapping ≤``cap`` content-token chunks.

    Each required term (including multi-word names) is atomic: a window never
    splits it. Names are included when they appear in ``terms``.
    """
    units: list[tuple[str, int]] = []
    seen: set[str] = set()
    for term in terms:
        phrase = normalize(term)
        if not phrase or phrase in seen:
            continue
        count = len(content_tokens(phrase))
        if count == 0:
            continue
        seen.add(phrase)
        units.append((phrase, count))
    windows: list[str] = []
    current: list[str] = []
    current_n = 0
    for phrase, count in units:
        if current and current_n + count > cap:
            windows.append(" ".join(current))
            current = []
            current_n = 0
        current.append(phrase)
        current_n += count
    if current:
        windows.append(" ".join(current))
    return windows


def person_entities_match(text: str, person_id: str, people: Sequence[Person]) -> bool:
    return resolve_person_id(text, people) == person_id


def resolve_person_id(text: str, people: Sequence[Person]) -> str | None:
    named = [person for person in people if has_phrase(text, person.name)]
    if not named:
        return None
    unique = {
        person.id
        for person in named
        if sum(1 for other in people if normalize(other.name) == normalize(person.name)) == 1
    }
    if len({person.id for person in named}) == 1:
        return named[0].id
    if len(unique) == 1 and len({normalize(person.name) for person in named}) == 1:
        return next(iter(unique))
    qualified = [person for person in named if person.company and has_phrase(text, person.company)]
    ids = {person.id for person in qualified}
    if len(ids) == 1:
        return qualified[0].id
    return None


def roster_forms(person: Person) -> list[str]:
    name = normalize(person.name)
    company = normalize(person.company)
    forms = [name]
    if company:
        forms.append(f"{name} {company}")
    return forms


def extract_slot_values(text: str, people: Sequence[Person]) -> dict[str, set[str]]:
    """Person / date / amount values asserted in ``text``.

    * **person** — roster ``person_id`` whose full name is a contiguous phrase.
    * **date** — ``weekday:<day>`` or ``cal:MM-DD`` (weekday is never a calendar date).
    * **amount** — ``amt:<int>`` from digits or number-words. Quarters are not amounts.
    """
    return {
        "person": {person.id for person in people if has_phrase(text, person.name)},
        "date": extract_dates(text),
        "amount": extract_amounts(text),
    }


def extract_dates(text: str) -> set[str]:
    norm = normalize(text)
    padded = f" {norm} "
    out: set[str] = set()
    for weekday in _WEEKDAYS:
        if f" {weekday} " in padded:
            out.add(f"weekday:{weekday}")
    for match in (*_ISO_DATE.finditer(text), *_ISO_DATE_SPACED.finditer(norm)):
        out.add(f"cal:{int(match.group(2)):02d}-{int(match.group(3)):02d}")
    for match in _DAY_MONTH.finditer(norm):
        month = _MONTH_INDEX[match.group(2)]
        out.add(f"cal:{month:02d}-{int(match.group(1)):02d}")
    return out


def extract_amounts(text: str) -> set[str]:
    out: set[str] = set()
    for token in normalize(text).split():
        if token in _MONTHS or token in _WEEKDAYS or token.startswith("q") and len(token) == 2:
            continue
        if _DIGIT.match(token):
            out.add(f"amt:{int(token)}")
        elif token in _NUMBER_VALUE:
            out.add(f"amt:{_NUMBER_VALUE[token]}")
        elif token in _NUMBER_WORDS and token in _NUMBER_VALUE:
            out.add(f"amt:{_NUMBER_VALUE[token]}")
    return out


def fact_slot_values(fact: GoldFact, people: Sequence[Person]) -> dict[str, set[str]]:
    blob = " ".join([*fact.required_terms, *fact_alias_phrases(fact)])
    values = extract_slot_values(blob, people)
    if fact.person_id:
        values["person"].add(fact.person_id)
    return values


def fact_alias_phrases(fact: GoldFact) -> list[str]:
    phrases: list[str] = []
    for items in fact.aliases.values():
        phrases.extend(items)
    return phrases


def contradicts(
    claim: PredictedClaim,
    fact: GoldFact,
    day: DayGold,
    cited_text: str,
) -> bool:
    """True when the claim asserts a competing same-slot person/date/amount."""
    if claim.slot != fact.slot:
        return False
    fact_values = fact_slot_values(fact, day.people)
    claim_values = extract_slot_values(claim.text, day.people)
    deal_values = _deal_slot_values(day, fact.deal_id)
    evidence_values = extract_slot_values(cited_text, day.people)
    for kind in ("person", "date", "amount"):
        for value in claim_values[kind]:
            if value in fact_values[kind]:
                continue
            deal_ok = value in deal_values[kind]
            evidence_ok = value in evidence_values[kind]
            if deal_ok and evidence_ok:
                continue
            if (
                fact_values[kind]
                or value in deal_values[kind]
                or value in {person.id for person in day.people}
            ):
                if not (deal_ok and evidence_ok):
                    return True
    return False


def slot_modal_ok(claim: PredictedClaim, fact: GoldFact) -> bool:
    tokens = set(content_tokens(claim.text))
    body = normalize(claim.text)
    if claim.slot == "what_happened":
        if tokens & WHAT_FUTURE:
            return False
        return not any(has_phrase(body, phrase) for phrase in WHAT_FUTURE_PHRASES)
    if claim.slot == "next_step":
        if tokens & NEXT_HEDGES or tokens & NEXT_NEGATION:
            return False
        if _done_form_without_planned(tokens):
            return False
        first = content_tokens(claim.text)
        imperative = bool(first) and first[0] in ACTION_VERBS
        has_commit = bool(tokens & {"will", "should"}) or has_phrase(body, "need to")
        return has_commit or imperative
    if claim.slot == "who_involved":
        return True
    return False


def required_terms_present(claim_text: str, fact: GoldFact, people: Sequence[Person]) -> bool:
    if fact.slot == "who_involved" and fact.person_id:
        person = _person(people, fact.person_id)
        if person is None:
            return False
        return any(has_phrase(claim_text, form) for form in roster_forms(person))
    for term in fact.required_terms:
        options = [term, *fact.aliases.get(term, ())]
        if not any(has_phrase(claim_text, option) for option in options):
            return False
    return True


def identity_prediction(
    gold: DealCorpus,
    deal_of: Callable[[DayGold, GoldSegment], str],
    claims_of: Callable[[DayGold], list[PredictedClaim]] | None = None,
) -> PredictedCorpus:
    days: list[PredictedDay] = []
    for day in gold.days:
        segments = [
            PredictedSegment(
                id=segment.id,
                frame_ids=segment.frame_ids,
                deal=NONE if segment.privacy_bait else str(deal_of(day, segment)),
                started_at=segment.started_at,
            )
            for segment in day.segments
        ]
        claims = list(claims_of(day)) if claims_of is not None else []
        days.append(PredictedDay(name=day.name, segments=segments, claims=claims))
    return PredictedCorpus(days=days)


def baseline_all_none(gold: DealCorpus) -> PredictedCorpus:
    return identity_prediction(gold, lambda _day, _segment: NONE)


def baseline_one_deal_per_day(gold: DealCorpus) -> PredictedCorpus:
    return identity_prediction(gold, lambda day, _segment: f"day:{day.name}")


def baseline_one_deal_corpus(gold: DealCorpus) -> PredictedCorpus:
    return identity_prediction(gold, lambda _day, _segment: "corpus")


def baseline_one_deal_per_segment(gold: DealCorpus) -> PredictedCorpus:
    return identity_prediction(gold, lambda _day, segment: segment.id)


def baseline_group_by_app(gold: DealCorpus) -> PredictedCorpus:
    return identity_prediction(gold, lambda _day, segment: segment.bundle_id or NONE)


def baseline_group_by_company(gold: DealCorpus) -> PredictedCorpus:
    return identity_prediction(gold, lambda day, segment: company_cluster(segment, day, gold))


def baseline_empty_summary(gold: DealCorpus) -> PredictedCorpus:
    return identity_prediction(gold, lambda _day, segment: segment.deal)


def baseline_keyword_dump_over_cap(gold: DealCorpus) -> PredictedCorpus:
    return identity_prediction(
        gold,
        lambda _day, segment: segment.deal,
        claims_of=_over_cap_claims,
    )


def baseline_keyword_dump_within_cap(gold: DealCorpus) -> PredictedCorpus:
    return identity_prediction(
        gold,
        lambda _day, segment: segment.deal,
        claims_of=_within_cap_claims,
    )


def honest_prediction(gold: DealCorpus, root: Path | None = None) -> PredictedCorpus:
    base = root if root is not None else deal_gold_root()
    days: list[PredictedDay] = []
    for day in gold.days:
        claims = load_honest_claims(base / day.name)
        segments = [
            PredictedSegment(
                id=segment.id,
                frame_ids=segment.frame_ids,
                deal=NONE if segment.privacy_bait else segment.deal,
                started_at=segment.started_at,
            )
            for segment in day.segments
        ]
        days.append(PredictedDay(name=day.name, segments=segments, claims=claims))
    return PredictedCorpus(days=days)


def company_cluster(segment: GoldSegment, day: DayGold, corpus: DealCorpus | None = None) -> str:
    """Roster/fixture ``company`` as a whole token in ``window_title`` only."""
    companies = _company_values(day, corpus)
    title_tokens = normalize(segment.window_title).split()
    matches = [company for company in companies if _whole_token_match(title_tokens, company)]
    if not matches:
        return NONE
    matches.sort(key=lambda company: (-len(normalize(company)), normalize(company)))
    return normalize(matches[0])


def deal_term_list(day: DayGold, deal_id: str) -> list[str]:
    """Required terms of one deal-day, including roster names, file order."""
    terms: list[str] = []
    for fact in day.facts:
        if fact.deal_id != deal_id:
            continue
        if fact.person_id:
            person = _person(day.people, fact.person_id)
            if person is not None:
                terms.append(person.name)
        terms.extend(fact.required_terms)
    return terms


def corpus_guidance(gold: DealCorpus) -> dict[str, float]:
    deals = {deal.id for day in gold.days for deal in day.deals}
    multi = 0
    for day in gold.days:
        for deal in day.deals:
            channels = {
                segment.channel
                for segment in day.segments
                if segment.deal == deal.id and not segment.privacy_bait
            }
            if {"email", "call", "doc"} <= channels:
                multi += 1
    same_company = _same_company_pairs(gold)
    lookalikes = {
        frozenset({deal.id, deal.lookalike_of})
        for day in gold.days
        for deal in day.deals
        if deal.lookalike_of
    }
    scored = [segment for day in gold.days for segment in day.segments if not segment.privacy_bait]
    noise = sum(1 for segment in scored if segment.deal == NONE)
    bait = sum(1 for day in gold.days for segment in day.segments if segment.privacy_bait)
    what_facts = sum(1 for day in gold.days for fact in day.facts if fact.slot == "what_happened")
    deal_days = sum(len(day.deals) for day in gold.days)
    return {
        "days": float(len(gold.days)),
        "deals": float(len(deals)),
        "multi_touch": float(multi),
        "same_company_pairs": float(len(same_company)),
        "lookalike_pairs": float(len(lookalikes)),
        "noise_rate": (noise / len(scored)) if scored else 0.0,
        "privacy_bait": float(bait),
        "what_facts_per_deal_day": (what_facts / deal_days) if deal_days else 0.0,
        "deal_days": float(deal_days),
        "what_facts": float(what_facts),
    }


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required to load deal gold fixtures")
    if not path.is_file():
        raise FileNotFoundError(path)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: YAML must be a mapping")
    return loaded


def _parse_day(truth: dict[str, Any], *, fallback_name: str) -> DayGold:
    name = str(truth.get("day") or fallback_name)
    date = str(truth.get("date") or "")
    deals = [
        Deal(
            id=str(row.get("id") or ""),
            name=str(row.get("name") or ""),
            company=str(row.get("company") or ""),
            lookalike_of=(str(row["lookalike_of"]) if row.get("lookalike_of") else None),
        )
        for row in _row_list(truth.get("deals"))
    ]
    people = [
        Person(
            id=str(row.get("id") or ""),
            name=str(row.get("name") or ""),
            company=str(row.get("company") or ""),
        )
        for row in _row_list(truth.get("people"))
    ]
    skipped: dict[str, Frame] = {}
    for row in _row_list(truth.get("skipped")):
        frame_id = str(row.get("frame_id") or "")
        skipped[frame_id] = Frame(
            id=frame_id,
            text="",
            skipped=True,
            skip_reason=str(row.get("reason") or ""),
            bundle_id=str(row.get("bundle_id") or ""),
        )
    frames: dict[str, Frame] = {}
    raw_frames = truth.get("frames") or {}
    if not isinstance(raw_frames, dict):
        raise ValueError(f"{name}: frames must be a mapping")
    for frame_id, raw in raw_frames.items():
        if isinstance(raw, str):
            text, extra = raw, {}
        else:
            extra = _mapping(raw, f"{name} frame {frame_id}")
            text = str(extra.get("text") or "")
        skip = skipped.get(str(frame_id))
        frames[str(frame_id)] = Frame(
            id=str(frame_id),
            text=text,
            skipped=skip is not None or bool(extra.get("skipped")),
            skip_reason=skip.skip_reason if skip is not None else str(extra.get("reason") or ""),
            bundle_id=skip.bundle_id if skip is not None else str(extra.get("bundle_id") or ""),
        )
    for frame_id, skip in skipped.items():
        frames.setdefault(frame_id, skip)
    segments = [
        GoldSegment(
            id=str(row.get("id") or ""),
            frame_ids=tuple(_string_list(row.get("frame_ids"), "frame_ids")),
            deal=str(row.get("deal") if row.get("deal") is not None else NONE),
            bundle_id=str(row.get("bundle_id") or ""),
            window_title=str(row.get("window_title") or ""),
            started_at=str(row.get("started_at") or ""),
            channel=str(row.get("channel") or ""),
            privacy_bait=bool(row.get("privacy_bait")),
        )
        for row in _row_list(truth.get("segments"))
    ]
    facts: list[GoldFact] = []
    summaries = truth.get("deal_summaries") or {}
    if summaries and not isinstance(summaries, dict):
        raise ValueError(f"{name}: deal_summaries must be a mapping")
    if isinstance(summaries, dict):
        for deal_id, slots in summaries.items():
            slot_map = _mapping(slots, f"{name} deal_summaries.{deal_id}")
            for slot in SLOTS:
                for row in _row_list(slot_map.get(slot)):
                    aliases_raw = row.get("aliases") or {}
                    aliases = {
                        str(term): tuple(str(item) for item in phrases)
                        for term, phrases in _mapping(aliases_raw, "aliases").items()
                    }
                    facts.append(
                        GoldFact(
                            id=str(row.get("id") or ""),
                            deal_id=str(deal_id),
                            slot=slot,
                            required_terms=tuple(
                                _string_list(row.get("required_terms"), "required_terms")
                            ),
                            aliases=aliases,
                            evidence_segments=tuple(
                                _string_list(row.get("evidence_segments"), "evidence_segments")
                            ),
                            person_id=(str(row["person_id"]) if row.get("person_id") else None),
                        )
                    )
    return DayGold(
        name=name,
        date=date,
        deals=deals,
        people=people,
        segments=segments,
        frames=frames,
        facts=facts,
        skipped=skipped,
    )


def _expand_acme_1211(truth: dict[str, Any]) -> dict[str, Any]:
    date = str(truth.get("date") or "2026-01-15")
    day_name = str(truth.get("day") or FAILING_ACME_1211)
    deals_out: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    frames: dict[str, Any] = {}
    for deal_row in _row_list(truth.get("deals")):
        deal_id = str(deal_row.get("id") or "")
        company = str(deal_row.get("company") or "")
        count = int(deal_row.get("n") or deal_row.get("segments") or 0)
        deals_out.append(
            {
                "id": deal_id,
                "name": str(deal_row.get("name") or deal_id),
                "company": company,
            }
        )
        for index in range(count):
            segment_id = f"s-{deal_id}-{index:02d}"
            frame_id = f"f-{deal_id}-{index:02d}"
            segments.append(
                {
                    "id": segment_id,
                    "frame_ids": [frame_id],
                    "deal": deal_id,
                    "bundle_id": "com.lumen.mail" if index % 2 == 0 else "com.lumen.quill",
                    "window_title": f"{deal_row.get('name') or deal_id} — {company}",
                    "started_at": f"{date}T{9 + (index % 8):02d}:{index:02d}:00Z",
                    "channel": "email" if index % 2 == 0 else "doc",
                }
            )
            frames[frame_id] = {"text": f"{deal_id} token{index} {company}"}
    noise = int(truth.get("noise") or 0)
    for index in range(noise):
        segment_id = f"s-noise-{index:02d}"
        frame_id = f"f-noise-{index:02d}"
        segments.append(
            {
                "id": segment_id,
                "frame_ids": [frame_id],
                "deal": NONE,
                "bundle_id": "com.lumen.compass",
                "window_title": f"Cedar Daily — tide {index}",
                "started_at": f"{date}T18:{index:02d}:00Z",
                "channel": "noise",
            }
        )
        frames[frame_id] = {"text": f"news tide table {index}"}
    return {
        "day": day_name,
        "date": date,
        "deals": deals_out,
        "people": [],
        "segments": segments,
        "frames": frames,
        "skipped": [],
        "deal_summaries": {},
    }


def _assignment_correct(gold_deal: str, pred: str, mapping: dict[str, str]) -> bool:
    if gold_deal == NONE:
        return pred == NONE
    if pred in {NONE, MISSING}:
        return False
    return mapping.get(pred) == gold_deal


def _day_pairs(
    segments: Sequence[GoldSegment], labels: dict[str, str]
) -> tuple[set[frozenset[str]], set[frozenset[str]]]:
    gold_pairs: set[frozenset[str]] = set()
    pred_pairs: set[frozenset[str]] = set()
    for left, right in _segment_pairs(segments):
        pair = frozenset({left.id, right.id})
        if left.deal == right.deal and left.deal != NONE:
            gold_pairs.add(pair)
        pred_left = labels.get(left.id, MISSING)
        pred_right = labels.get(right.id, MISSING)
        if pred_left == pred_right and pred_left not in {NONE, MISSING}:
            pred_pairs.add(pair)
    return gold_pairs, pred_pairs


def _segment_pairs(segments: Sequence[GoldSegment]) -> Iterator[tuple[GoldSegment, GoldSegment]]:
    ordered = list(segments)
    for i, left in enumerate(ordered):
        for right in ordered[i + 1 :]:
            yield left, right


def _deal_non_bait_count(day: DayGold, deal_id: str) -> int:
    return sum(
        1 for segment in day.segments if segment.deal == deal_id and not segment.privacy_bait
    )


def _pair_claims(
    day: DayGold,
    pred: PredictedDay,
    mapping: dict[str, str],
) -> list[tuple[PredictedClaim, GoldFact]]:
    used: set[str] = set()
    paired: list[tuple[PredictedClaim, GoldFact]] = []
    inverse = {gold_id: pred_id for pred_id, gold_id in mapping.items()}
    for fact in day.facts:
        pred_id = inverse.get(fact.deal_id)
        if pred_id is None:
            continue
        for claim in pred.claims:
            if claim.id in used or claim.deal != pred_id or claim.slot != fact.slot:
                continue
            if _claim_covers(day, pred, mapping, claim, fact):
                used.add(claim.id)
                paired.append((claim, fact))
                break
    return paired


def _claim_covers(
    day: DayGold,
    pred: PredictedDay,
    mapping: dict[str, str],
    claim: PredictedClaim,
    fact: GoldFact,
) -> bool:
    if claim.slot != fact.slot or claim.slot not in SLOTS:
        return False
    if mapping.get(claim.deal) != fact.deal_id:
        return False
    if len(content_tokens(claim.text)) > CONTENT_TOKEN_CAP:
        return False
    if not required_terms_present(claim.text, fact, day.people):
        return False
    if fact.slot == "who_involved" and fact.person_id:
        if not person_entities_match(claim.text, fact.person_id, day.people):
            return False
    frames = resolve_claim_frames(claim, pred, day)
    if frames is None:
        return False
    cited_segments = _gold_segments_for_frames(day, frames)
    if not set(cited_segments) & set(fact.evidence_segments):
        return False
    cited_text = _ocr(day, frames)
    if contradicts(claim, fact, day, cited_text):
        return False
    if not slot_modal_ok(claim, fact):
        return False
    return True


def _unsupported(
    day: DayGold,
    pred: PredictedDay,
    mapping: dict[str, str],
    claim: PredictedClaim,
    fact: GoldFact | None,
) -> bool:
    frames = resolve_claim_frames(claim, pred, day)
    if frames is None:
        return True
    matched_gold = mapping.get(claim.deal)
    if matched_gold is not None:
        deal_frames = _deal_frame_ids(day, matched_gold)
        if not set(frames) & deal_frames:
            return True
    else:
        cluster_frames = _predicted_cluster_frames(pred, claim.deal)
        if not set(frames) & cluster_frames:
            return True
        return False
    if fact is None:
        return False
    cited_segments = _gold_segments_for_frames(day, frames)
    if not set(cited_segments) & set(fact.evidence_segments):
        return True
    cited_text = _ocr(day, frames)
    if fact.slot == "who_involved" and fact.person_id:
        person = _person(day.people, fact.person_id)
        if person is None or not any(has_phrase(cited_text, form) for form in roster_forms(person)):
            return True
        return False
    return not required_terms_present(cited_text, fact, day.people)


def resolve_claim_frames(
    claim: PredictedClaim, pred: PredictedDay, day: DayGold
) -> list[str] | None:
    if not claim.evidence:
        return None
    frames: list[str] = []
    pred_by_id = {segment.id: segment for segment in pred.segments}
    gold_frames = set(day.frames)
    for evidence in claim.evidence:
        if evidence.frame_id:
            if evidence.frame_id not in gold_frames:
                return None
            frames.append(evidence.frame_id)
            continue
        if evidence.segment_id:
            segment = pred_by_id.get(evidence.segment_id)
            if segment is None:
                segment = _pred_from_gold(day, evidence.segment_id)
            if segment is None:
                return None
            if not segment.frame_ids:
                return None
            frames.extend(segment.frame_ids)
            continue
        return None
    return frames or None


def _pred_from_gold(day: DayGold, segment_id: str) -> PredictedSegment | None:
    segment = _gold_segment(day, segment_id)
    if segment is None:
        return None
    return PredictedSegment(
        id=segment.id,
        frame_ids=segment.frame_ids,
        deal=segment.deal,
        started_at=segment.started_at,
    )


def _gold_segments_for_frames(day: DayGold, frames: Sequence[str]) -> list[str]:
    wanted = set(frames)
    return [segment.id for segment in day.segments if wanted & set(segment.frame_ids)]


def _deal_frame_ids(day: DayGold, deal_id: str) -> set[str]:
    return {
        frame_id
        for segment in day.segments
        if segment.deal == deal_id and not segment.privacy_bait
        for frame_id in segment.frame_ids
    }


def _predicted_cluster_frames(pred: PredictedDay, cluster_id: str) -> set[str]:
    return {
        frame_id
        for segment in pred.segments
        if segment.deal == cluster_id
        for frame_id in segment.frame_ids
    }


def _ocr(day: DayGold, frames: Sequence[str]) -> str:
    return " ".join(day.frames[frame_id].text for frame_id in frames if frame_id in day.frames)


def _gold_segment(day: DayGold, segment_id: str) -> GoldSegment | None:
    for segment in day.segments:
        if segment.id == segment_id:
            return segment
    return None


def _person(people: Sequence[Person], person_id: str) -> Person | None:
    for person in people:
        if person.id == person_id:
            return person
    return None


def _deal_slot_values(day: DayGold, deal_id: str) -> dict[str, set[str]]:
    values: dict[str, set[str]] = {"person": set(), "date": set(), "amount": set()}
    for fact in day.facts:
        if fact.deal_id != deal_id:
            continue
        for kind, items in fact_slot_values(fact, day.people).items():
            values[kind].update(items)
    return values


def _done_form_without_planned(tokens: set[str]) -> bool:
    for token in tokens:
        if token not in _DONE_SURFACES:
            continue
        lemma = _STATUS_LEMMA[token]
        if lemma not in tokens and lemma in _PLANNED_SURFACES:
            return True
    return False


def _deal_segments(day: DayGold, deal_id: str) -> list[GoldSegment]:
    return [
        segment for segment in day.segments if segment.deal == deal_id and not segment.privacy_bait
    ]


def _over_cap_claims(day: DayGold) -> list[PredictedClaim]:
    claims: list[PredictedClaim] = []
    for deal in day.deals:
        segs = _deal_segments(day, deal.id)
        tokens: list[str] = []
        seen: set[str] = set()
        for segment in segs:
            for frame_id in segment.frame_ids:
                frame = day.frames.get(frame_id)
                if frame is None:
                    continue
                for token in content_tokens(frame.text):
                    if token in seen:
                        continue
                    seen.add(token)
                    tokens.append(token)
        text = " ".join(tokens)
        evidence = [PredictedEvidence(segment_id=segment.id) for segment in segs]
        for fact in day.facts:
            if fact.deal_id != deal.id:
                continue
            claims.append(
                PredictedClaim(
                    id=f"dump-over-{day.name}-{fact.id}",
                    deal=deal.id,
                    slot=fact.slot,
                    text=text,
                    evidence=list(evidence),
                )
            )
    return claims


def _within_cap_claims(day: DayGold) -> list[PredictedClaim]:
    claims: list[PredictedClaim] = []
    for deal in day.deals:
        segs = _deal_segments(day, deal.id)
        evidence = [PredictedEvidence(segment_id=segment.id) for segment in segs]
        windows = dump_windows(deal_term_list(day, deal.id))
        for window_index, window in enumerate(windows):
            for slot in SLOTS:
                claims.append(
                    PredictedClaim(
                        id=f"dump-in-{day.name}-{deal.id}-{window_index}-{slot}",
                        deal=deal.id,
                        slot=slot,
                        text=window,
                        evidence=list(evidence),
                    )
                )
    return claims


def _company_values(day: DayGold, corpus: DealCorpus | None = None) -> list[str]:
    days = corpus.days if corpus is not None else [day]
    values: list[str] = []
    seen: set[str] = set()
    for item in days:
        for person in item.people:
            company = person.company.strip()
            key = normalize(company)
            if company and key not in seen:
                seen.add(key)
                values.append(company)
        for deal in item.deals:
            company = deal.company.strip()
            key = normalize(company)
            if company and key not in seen:
                seen.add(key)
                values.append(company)
    return values


def _whole_token_match(title_tokens: Sequence[str], company: str) -> bool:
    company_tokens = normalize(company).split()
    if not company_tokens:
        return False
    if len(company_tokens) == 1:
        return company_tokens[0] in title_tokens
    size = len(company_tokens)
    for index in range(len(title_tokens) - size + 1):
        if list(title_tokens[index : index + size]) == company_tokens:
            return True
    return False


def _same_company_pairs(gold: DealCorpus) -> set[frozenset[str]]:
    by_company: dict[str, set[str]] = {}
    for day in gold.days:
        for deal in day.deals:
            company = normalize(deal.company)
            if not company:
                continue
            by_company.setdefault(company, set()).add(deal.id)
    pairs: set[frozenset[str]] = set()
    for deal_ids in by_company.values():
        ordered = sorted(deal_ids)
        for i, left in enumerate(ordered):
            for right in ordered[i + 1 :]:
                pairs.add(frozenset({left, right}))
    return pairs


def _bait_frame_ids(gold: DealCorpus) -> set[str]:
    return {
        frame_id
        for day in gold.days
        for segment in day.segments
        if segment.privacy_bait
        for frame_id in segment.frame_ids
    }


def _bait_markers(gold: DealCorpus) -> set[str]:
    markers: set[str] = {"privacybait"}
    for day in gold.days:
        for segment in day.segments:
            if not segment.privacy_bait:
                continue
            for frame_id in segment.frame_ids:
                frame = day.frames.get(frame_id)
                if frame is None:
                    continue
                tokens = content_tokens(frame.text)
                if "privacybait" in tokens:
                    markers.add("privacybait")
                markers.add(normalize(frame.text))
    return {item for item in markers if item}


def _contains_marker(text: str, markers: set[str]) -> bool:
    if not text:
        return False
    norm = normalize(text)
    if "privacybait" in norm.split():
        return True
    padded = f" {norm} "
    return any(len(marker.split()) >= 3 and f" {marker} " in padded for marker in markers)


def _validate_privacy_bait(day: DayGold) -> None:
    skipped_excluded = {
        frame_id for frame_id, frame in day.skipped.items() if frame.skip_reason in EXCLUDED_REASONS
    }
    for segment in day.segments:
        if not segment.privacy_bait:
            if segment.frame_ids and set(segment.frame_ids) <= skipped_excluded:
                raise ValueError(
                    f"{day.name}: segment {segment.id} is fully excluded and must set privacy_bait"
                )
            continue
        if not segment.frame_ids:
            raise ValueError(f"{day.name}: privacy_bait segment {segment.id} has no frames")
        for frame_id in segment.frame_ids:
            if frame_id not in day.frames:
                raise ValueError(
                    f"{day.name}: privacy_bait frame {frame_id} is missing from the fixture"
                )
            skip = day.skipped.get(frame_id)
            if skip is None or skip.skip_reason not in EXCLUDED_REASONS:
                raise ValueError(
                    f"{day.name}: privacy_bait frame {frame_id} is not skipped excluded_app/title"
                )
    leftover = skipped_excluded - {
        frame_id for segment in day.segments for frame_id in segment.frame_ids
    }
    if leftover:
        raise ValueError(
            f"{day.name}: skipped excluded frames {sorted(leftover)} are not on a gold segment"
        )


def _validate_aliases(day: DayGold) -> None:
    deal_terms: dict[str, set[str]] = {}
    for fact in day.facts:
        terms = {normalize(term) for term in fact.required_terms}
        terms.update(normalize(phrase) for phrase in fact_alias_phrases(fact))
        if fact.person_id:
            person = _person(day.people, fact.person_id)
            if person is not None:
                terms.add(normalize(person.name))
        deal_terms.setdefault(fact.deal_id, set()).update(terms)
    for fact in day.facts:
        other_deals = {
            term
            for deal_id, terms in deal_terms.items()
            if deal_id != fact.deal_id
            for term in terms
        }
        same_deal = [
            other for other in day.facts if other.deal_id == fact.deal_id and other.id != fact.id
        ]
        fact_terms = {normalize(term) for term in fact.required_terms}
        for term, phrases in fact.aliases.items():
            if len(phrases) > 3:
                raise ValueError(f"{day.name}: fact {fact.id} term {term} has more than 3 aliases")
            for phrase in phrases:
                if not content_tokens(phrase):
                    raise ValueError(
                        f"{day.name}: fact {fact.id} alias {phrase!r} is stopwords-only"
                    )
                if _weekday_date_alias(term, phrase):
                    raise ValueError(f"{day.name}: fact {fact.id} aliases weekday to a date")
                norm = normalize(phrase)
                if norm in other_deals:
                    raise ValueError(
                        f"{day.name}: fact {fact.id} alias {phrase!r} is a cross-deal term"
                    )
                if _exclusive_other_term(norm, fact_terms, same_deal):
                    raise ValueError(
                        f"{day.name}: fact {fact.id} alias {phrase!r} is exclusive to another fact"
                    )


def _weekday_date_alias(term: str, phrase: str) -> bool:
    term_dates = extract_dates(term)
    phrase_dates = extract_dates(phrase)
    weekdays = {item for item in term_dates | phrase_dates if item.startswith("weekday:")}
    calendars = {item for item in term_dates | phrase_dates if item.startswith("cal:")}
    return bool(weekdays and calendars)


def _exclusive_other_term(phrase: str, fact_terms: set[str], others: Sequence[GoldFact]) -> bool:
    if phrase in fact_terms:
        return False
    for other in others:
        other_terms = {normalize(term) for term in other.required_terms}
        if phrase not in other_terms:
            continue
        shared = any(
            phrase in {normalize(term) for term in extra.required_terms}
            for extra in others
            if extra.id != other.id
        )
        if not shared:
            return True
    return False


def _mapping(raw: Any, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a mapping")
    return raw


def _row_list(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("expected a list of mappings")
    return [_mapping(item, "row") for item in raw]


def _string_list(raw: Any, label: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{label} must be a list")
    return [str(item) for item in raw]


__all__ = [
    "CONTENT_TOKEN_CAP",
    "FAILING_ACME_1211",
    "MISSING",
    "NONE",
    "SLOTS",
    "V1_DEAL_DAYS",
    "DealCorpus",
    "DealScore",
    "DayGold",
    "GoldFact",
    "GoldSegment",
    "GroupingScore",
    "Person",
    "PredictedClaim",
    "PredictedCorpus",
    "PredictedDay",
    "PredictedEvidence",
    "PredictedSegment",
    "SummaryScore",
    "align_segments",
    "baseline_all_none",
    "baseline_empty_summary",
    "baseline_group_by_app",
    "baseline_group_by_company",
    "baseline_keyword_dump_over_cap",
    "baseline_keyword_dump_within_cap",
    "baseline_one_deal_corpus",
    "baseline_one_deal_per_day",
    "baseline_one_deal_per_segment",
    "company_cluster",
    "content_tokens",
    "corpus_guidance",
    "deal_gold_root",
    "deal_term_list",
    "dump_windows",
    "extract_amounts",
    "extract_dates",
    "extract_slot_values",
    "has_phrase",
    "honest_prediction",
    "identity_prediction",
    "load_deal_corpus",
    "load_deal_day",
    "load_failing_acme_1211",
    "load_honest_claims",
    "match_clusters",
    "person_entities_match",
    "privacy_bait_legal",
    "privacy_leak",
    "resolve_person_id",
    "score_deals",
    "score_grouping",
    "score_summary",
    "validate_corpus",
    "validate_day",
]

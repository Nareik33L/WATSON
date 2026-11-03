"""Deal groups for Today: title, cited slots, linked channels (plan §8 / M3f).

No merged text, OCR, snippets, or other tier-2 fields. Skipped (excluded-app)
start frames never appear. ``none`` segments stay in their own section.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.intelligence.citations import CITATION_RE
from watson.pipeline.frames import PIPELINE_VERSION

SLOT_ORDER = ("what_happened", "who_involved", "next_step")
SLOT_LABELS = {
    "what_happened": "What happened",
    "who_involved": "Who involved",
    "next_step": "Next step",
}
_SLOT_PREFIXES: dict[str, tuple[str, ...]] = {
    "what_happened": ("what happened:",),
    "who_involved": ("who involved:", "who was involved:"),
    "next_step": ("next step:",),
}
_EMAIL_TOKENS = ("mail", "email", "outlook", "gmail")
_CALL_TOKENS = ("relay", "call", "meet", "zoom", "teams", "facetime")
_DOC_TOKENS = ("quill", "pages", "docs", "word", "grid", "sheet", "slides", "anvil", "document")


@dataclass(frozen=True)
class CitedSpan:
    """Narrative fragment. Citations point at the evidence segment, not a claim list."""

    text: str
    claim_id: str | None
    segment_id: str | None


@dataclass(frozen=True)
class DealSlotView:
    slot: str
    label: str
    spans: tuple[CitedSpan, ...]


@dataclass(frozen=True)
class DealEvidence:
    """One linked segment. App / title / dwell only — never merged text."""

    id: str
    app_name: str | None
    window_title: str | None
    started_at: str
    ended_at: str
    dwell_seconds: int
    channel: str
    observation: str | None = None

    def duration_label(self) -> str:
        hours, rem = divmod(max(0, self.dwell_seconds), 3600)
        minutes, seconds = divmod(rem, 60)
        if hours:
            return f"{hours}h {minutes:02d}m"
        if minutes:
            return f"{minutes}m"
        return f"{seconds}s"

    def range_label(self) -> str:
        return f"{_hhmm(self.started_at)}–{_hhmm(self.ended_at)}"


@dataclass(frozen=True)
class DealCard:
    id: str
    title: str
    slots: tuple[DealSlotView, ...]
    emails: tuple[DealEvidence, ...]
    calls: tuple[DealEvidence, ...]
    docs: tuple[DealEvidence, ...]
    other: tuple[DealEvidence, ...] = ()

    def channel_rows(self) -> tuple[tuple[str, tuple[DealEvidence, ...]], ...]:
        rows: tuple[tuple[str, tuple[DealEvidence, ...]], ...] = (
            ("Emails", self.emails),
            ("Calls", self.calls),
            ("Docs", self.docs),
        )
        if self.other:
            return (*rows, ("Other", self.other))
        return rows


@dataclass(frozen=True)
class DealGroups:
    deals: tuple[DealCard, ...] = ()
    none_segments: tuple[DealEvidence, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "deals": [_card_dict(card) for card in self.deals],
            "none_segments": [_evidence_dict(item) for item in self.none_segments],
        }


def collect_deal_groups(
    conn: sqlcipher.Connection,
    *,
    working_day_id: str | None,
    pipeline_version: int = PIPELINE_VERSION,
) -> DealGroups:
    """Load deal cards and the unassigned ``none`` section for one working day."""
    if not working_day_id:
        return DealGroups()
    deals = _deals(conn, working_day_id, pipeline_version)
    evidence = _evidence(conn, working_day_id, pipeline_version)
    claim_segments = _claim_segments(conn, working_day_id, pipeline_version)
    claims = _claim_texts(conn, working_day_id, pipeline_version)
    narrative, sections = _summary(conn, working_day_id, pipeline_version)
    blocks = _heading_blocks(narrative)
    slots_by_deal = _slots_by_deal(sections)
    by_deal: dict[str, list[DealEvidence]] = {row["id"]: [] for row in deals}
    none: list[DealEvidence] = []
    for item in evidence:
        deal_id = item[0]
        row = item[1]
        if deal_id is None or deal_id not in by_deal:
            none.append(row)
        else:
            by_deal[deal_id].append(row)
    unused_blocks = list(blocks)
    cards: list[DealCard] = []
    for deal in deals:
        body = _take_block(unused_blocks, str(deal["title"]))
        parsed = _parse_slot_text(body)
        slot_ids = slots_by_deal.get(str(deal["id"]), {})
        slots = tuple(
            DealSlotView(
                slot=slot,
                label=SLOT_LABELS[slot],
                spans=_slot_spans(
                    parsed.get(slot) or _claims_as_text(slot_ids.get(slot, ()), claims),
                    claim_segments,
                ),
            )
            for slot in SLOT_ORDER
        )
        grouped = _by_channel(by_deal.get(str(deal["id"]), ()))
        cards.append(
            DealCard(
                id=str(deal["id"]),
                title=str(deal["title"]),
                slots=slots,
                emails=grouped["email"],
                calls=grouped["call"],
                docs=grouped["doc"],
                other=grouped["other"],
            )
        )
    return DealGroups(deals=tuple(cards), none_segments=tuple(none))


def segment_channel(app_name: str | None, window_title: str | None) -> str:
    """Map a visible app/title to email, call, doc, or other. Title is not unique."""
    blob = f"{app_name or ''} {window_title or ''}".casefold()
    if any(token in blob for token in _EMAIL_TOKENS):
        return "email"
    if any(token in blob for token in _CALL_TOKENS):
        return "call"
    if any(token in blob for token in _DOC_TOKENS):
        return "doc"
    return "other"


def _deals(
    conn: sqlcipher.Connection, working_day_id: str, pipeline_version: int
) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT id, title
        FROM deals
        WHERE working_day_id = ? AND pipeline_version = ?
        ORDER BY created_at ASC, id ASC
        """,
        (working_day_id, pipeline_version),
    ).fetchall()
    return [{"id": str(row["id"]), "title": str(row["title"])} for row in rows]


def _evidence(
    conn: sqlcipher.Connection, working_day_id: str, pipeline_version: int
) -> list[tuple[str | None, DealEvidence]]:
    rows = conn.execute(
        """
        SELECT
            s.id AS id,
            s.deal_id AS deal_id,
            s.started_at AS started_at,
            s.ended_at AS ended_at,
            s.dwell_seconds AS dwell_seconds,
            f.app_name AS app_name,
            f.window_title AS window_title,
            f.state AS frame_state,
            o.description AS description
        FROM segments s
        LEFT JOIN frames f ON f.id = s.start_frame_id
        LEFT JOIN observations o
            ON o.segment_id = s.id AND o.pipeline_version = ?
        WHERE s.working_day_id = ?
        ORDER BY s.started_at ASC, s.id ASC
        """,
        (pipeline_version, working_day_id),
    ).fetchall()
    out: list[tuple[str | None, DealEvidence]] = []
    for row in rows:
        if str(row["frame_state"] or "") == "skipped":
            continue
        app = None if row["app_name"] is None else str(row["app_name"])
        title = None if row["window_title"] is None else str(row["window_title"])
        deal_id = None if row["deal_id"] is None else str(row["deal_id"])
        item = DealEvidence(
            id=str(row["id"]),
            app_name=app,
            window_title=title,
            started_at=str(row["started_at"]),
            ended_at=str(row["ended_at"]),
            dwell_seconds=int(row["dwell_seconds"] or 0),
            channel=segment_channel(app, title),
            observation=None if row["description"] is None else str(row["description"]),
        )
        out.append((deal_id, item))
    return out


def _claim_segments(
    conn: sqlcipher.Connection, working_day_id: str, pipeline_version: int
) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT e.claim_id AS claim_id, e.segment_id AS segment_id
        FROM claim_evidence e
        JOIN claims c ON c.id = e.claim_id
        LEFT JOIN segments s ON s.id = e.segment_id
        LEFT JOIN frames f ON f.id = s.start_frame_id
        WHERE c.working_day_id = ?
          AND c.pipeline_version = ?
          AND e.segment_id IS NOT NULL
          AND (f.state IS NULL OR f.state != 'skipped')
        ORDER BY e.created_at ASC, e.id ASC
        """,
        (working_day_id, pipeline_version),
    ).fetchall()
    mapped: dict[str, str] = {}
    for row in rows:
        cid = str(row["claim_id"])
        if cid not in mapped:
            mapped[cid] = str(row["segment_id"])
    return mapped


def _claim_texts(
    conn: sqlcipher.Connection, working_day_id: str, pipeline_version: int
) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT id, text FROM claims
        WHERE working_day_id = ? AND pipeline_version = ?
        """,
        (working_day_id, pipeline_version),
    ).fetchall()
    return {str(row["id"]): str(row["text"]) for row in rows}


def _summary(
    conn: sqlcipher.Connection, working_day_id: str, pipeline_version: int
) -> tuple[str, dict[str, Any]]:
    row = conn.execute(
        """
        SELECT narrative_md, sections_json
        FROM daily_summaries
        WHERE working_day_id = ? AND pipeline_version = ?
        """,
        (working_day_id, pipeline_version),
    ).fetchone()
    if row is None:
        return "", {}
    raw = row["sections_json"]
    loaded: dict[str, Any] = {}
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            loaded = parsed
    return str(row["narrative_md"] or ""), loaded


def _slots_by_deal(sections: dict[str, Any]) -> dict[str, dict[str, tuple[str, ...]]]:
    items = sections.get("slots") if isinstance(sections.get("slots"), list) else []
    by_deal: dict[str, dict[str, tuple[str, ...]]] = {}
    if not isinstance(items, list):
        return by_deal
    for item in items:
        if not isinstance(item, dict):
            continue
        deal_id = str(item.get("deal_id") or "")
        slot = str(item.get("slot") or "")
        raw_ids = item.get("claim_ids")
        if not deal_id or slot not in SLOT_LABELS or not isinstance(raw_ids, list):
            continue
        claim_ids = tuple(str(cid) for cid in raw_ids if cid)
        if not claim_ids:
            continue
        by_deal.setdefault(deal_id, {})[slot] = claim_ids
    return by_deal


_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _heading_blocks(narrative: str) -> list[tuple[str, str]]:
    if not narrative.strip():
        return []
    matches = list(_HEADING.finditer(narrative))
    if not matches:
        return [("", narrative.strip())]
    blocks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(narrative)
        blocks.append((match.group(1).strip(), narrative[match.end() : end].strip()))
    return blocks


def _take_block(blocks: list[tuple[str, str]], title: str) -> str:
    for index, (heading, body) in enumerate(blocks):
        if heading == title:
            blocks.pop(index)
            return body
    if len(blocks) == 1 and blocks[0][0] == "":
        return blocks.pop(0)[1]
    return ""


def _parse_slot_text(body: str) -> dict[str, str]:
    found: dict[str, list[str]] = {slot: [] for slot in SLOT_ORDER}
    current: str | None = None
    unlabeled: list[str] = []
    for raw in body.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        folded = stripped.casefold()
        matched: str | None = None
        rest = stripped
        for slot, prefixes in _SLOT_PREFIXES.items():
            for prefix in prefixes:
                if folded.startswith(prefix):
                    matched = slot
                    rest = stripped[len(prefix) :].strip()
                    break
            if matched is not None:
                break
        if matched is not None:
            current = matched
            if rest:
                found[matched].append(rest)
            continue
        if current is not None:
            found[current].append(stripped)
        else:
            unlabeled.append(stripped)
    if unlabeled and not any(found.values()):
        found["what_happened"] = unlabeled
    return {slot: " ".join(parts) for slot, parts in found.items() if parts}


def _claims_as_text(claim_ids: tuple[str, ...], claims: dict[str, str]) -> str:
    parts: list[str] = []
    for cid in claim_ids:
        text = claims.get(cid)
        if not text:
            continue
        parts.append(f"{text} [c:{cid}]")
    return " ".join(parts)


def _slot_spans(text: str, claim_segments: dict[str, str]) -> tuple[CitedSpan, ...]:
    if not text:
        return ()
    out: list[CitedSpan] = []
    cursor = 0
    for match in CITATION_RE.finditer(text):
        if match.start() > cursor:
            out.append(CitedSpan(text[cursor : match.start()], None, None))
        claim_id = match.group(1)
        out.append(CitedSpan(match.group(0), claim_id, claim_segments.get(claim_id)))
        cursor = match.end()
    if cursor < len(text):
        out.append(CitedSpan(text[cursor:], None, None))
    return tuple(out)


def _by_channel(
    items: tuple[DealEvidence, ...] | list[DealEvidence],
) -> dict[str, tuple[DealEvidence, ...]]:
    buckets: dict[str, list[DealEvidence]] = {
        "email": [],
        "call": [],
        "doc": [],
        "other": [],
    }
    for item in items:
        key = item.channel if item.channel in buckets else "other"
        buckets[key].append(item)
    return {key: tuple(value) for key, value in buckets.items()}


def _card_dict(card: DealCard) -> dict[str, Any]:
    return {
        "id": card.id,
        "title": card.title,
        "slots": [
            {
                "slot": slot.slot,
                "label": slot.label,
                "spans": [
                    {
                        "text": span.text,
                        "claim_id": span.claim_id,
                        "segment_id": span.segment_id,
                    }
                    for span in slot.spans
                ],
            }
            for slot in card.slots
        ],
        "emails": [_evidence_dict(item) for item in card.emails],
        "calls": [_evidence_dict(item) for item in card.calls],
        "docs": [_evidence_dict(item) for item in card.docs],
        "other": [_evidence_dict(item) for item in card.other],
    }


def _evidence_dict(item: DealEvidence) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": item.id,
        "app_name": item.app_name,
        "window_title": item.window_title,
        "started_at": item.started_at,
        "ended_at": item.ended_at,
        "dwell_seconds": item.dwell_seconds,
        "channel": item.channel,
    }
    if item.observation is not None:
        row["observation"] = item.observation
    return row


def _hhmm(stamp: str) -> str:
    if "T" in stamp and len(stamp) >= 16:
        return stamp[11:16]
    return stamp

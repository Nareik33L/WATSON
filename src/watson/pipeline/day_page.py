"""Day page data: summary, claims, evidence quotes, and day list (plan §8)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.intelligence.citations import CITATION_RE
from watson.pipeline.frames import PIPELINE_VERSION


@dataclass(frozen=True)
class DayChoice:
    date: str
    state: str


@dataclass(frozen=True)
class NarrativeSpan:
    text: str
    claim_id: str | None


@dataclass(frozen=True)
class DayClaimView:
    id: str
    kind: str
    assertion: str
    text: str
    quotes: tuple[str, ...]


@dataclass(frozen=True)
class DayPage:
    id: str | None
    date: str | None
    state: str
    error: str | None
    choices: tuple[DayChoice, ...]
    spans: tuple[NarrativeSpan, ...]
    unverified: tuple[DayClaimView, ...]
    claims: tuple[DayClaimView, ...]
    cited_ids: tuple[str, ...]


def collect_day_page(
    conn: sqlcipher.Connection,
    *,
    date: str | None = None,
    pipeline_version: int = PIPELINE_VERSION,
) -> DayPage:
    """Load one working day for the dashboard, or the latest if ``date`` is omitted."""
    choices = _choices(conn)
    chosen = date if date is not None else (choices[0].date if choices else None)
    if chosen is None:
        return DayPage(
            id=None,
            date=None,
            state="none",
            error=None,
            choices=(),
            spans=(),
            unverified=(),
            claims=(),
            cited_ids=(),
        )
    day = conn.execute(
        """
        SELECT id, date, state, consolidation_error
        FROM working_days WHERE date = ?
        ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        (chosen,),
    ).fetchone()
    if day is None:
        return DayPage(
            id=None,
            date=chosen,
            state="missing",
            error=None,
            choices=choices,
            spans=(),
            unverified=(),
            claims=(),
            cited_ids=(),
        )
    day_id = str(day["id"])
    claims = _claims(conn, day_id, pipeline_version)
    by_id = {item.id: item for item in claims}
    summary = conn.execute(
        """
        SELECT narrative_md, sections_json, claim_ids
        FROM daily_summaries
        WHERE working_day_id = ? AND pipeline_version = ?
        """,
        (day_id, pipeline_version),
    ).fetchone()
    narrative = str(summary["narrative_md"]) if summary is not None else ""
    cited = tuple(CITATION_RE.findall(narrative))
    unverified = tuple(by_id[item] for item in _unverified_ids(summary) if item in by_id)
    return DayPage(
        id=day_id,
        date=str(day["date"]),
        state=str(day["state"]),
        error=None if day["consolidation_error"] is None else str(day["consolidation_error"]),
        choices=choices,
        spans=_spans(narrative),
        unverified=unverified,
        claims=claims,
        cited_ids=cited,
    )


def _choices(conn: sqlcipher.Connection) -> tuple[DayChoice, ...]:
    rows = conn.execute(
        "SELECT date, state FROM working_days ORDER BY date DESC, created_at DESC"
    ).fetchall()
    return tuple(DayChoice(date=str(row["date"]), state=str(row["state"])) for row in rows)


def _claims(
    conn: sqlcipher.Connection, working_day_id: str, pipeline_version: int
) -> tuple[DayClaimView, ...]:
    rows = conn.execute(
        """
        SELECT id, kind, assertion, text FROM claims
        WHERE working_day_id = ? AND pipeline_version = ?
        ORDER BY created_at, id
        """,
        (working_day_id, pipeline_version),
    ).fetchall()
    evidence = conn.execute(
        """
        SELECT e.claim_id AS claim_id, e.snippet AS quote
        FROM claim_evidence e
        JOIN claims c ON c.id = e.claim_id
        WHERE c.working_day_id = ?
        ORDER BY e.created_at, e.id
        """,
        (working_day_id,),
    ).fetchall()
    quotes: dict[str, list[str]] = {}
    for row in evidence:
        quotes.setdefault(str(row["claim_id"]), []).append(str(row["quote"]))
    return tuple(
        DayClaimView(
            id=str(row["id"]),
            kind=str(row["kind"]),
            assertion=str(row["assertion"]),
            text=str(row["text"]),
            quotes=tuple(quotes.get(str(row["id"]), ())),
        )
        for row in rows
    )


def _unverified_ids(summary: Any) -> tuple[str, ...]:
    if summary is None:
        return ()
    raw = summary["sections_json"]
    if not isinstance(raw, str):
        return ()
    loaded = json.loads(raw)
    items = loaded.get("unverified") if isinstance(loaded, dict) else None
    if not isinstance(items, list):
        return ()
    return tuple(str(item["id"]) for item in items if isinstance(item, dict) and item.get("id"))


def _spans(narrative: str) -> tuple[NarrativeSpan, ...]:
    if not narrative:
        return ()
    out: list[NarrativeSpan] = []
    cursor = 0
    for match in CITATION_RE.finditer(narrative):
        if match.start() > cursor:
            out.append(NarrativeSpan(narrative[cursor : match.start()], None))
        out.append(NarrativeSpan(match.group(0), match.group(1)))
        cursor = match.end()
    if cursor < len(narrative):
        out.append(NarrativeSpan(narrative[cursor:], None))
    return tuple(out)

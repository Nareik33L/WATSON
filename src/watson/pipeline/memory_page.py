"""Memory page: claims with assertion badges, evidence quotes, corrections.

Read of claims is scoped by working day and ``pipeline_version``. Evidence
quotes are tier-3 snippets already stored on claims. Tier-2 OCR / merged
text is never loaded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.memory.corrections import claim_histories, overlay_from_history
from watson.pipeline.day_page import DayChoice, _choices
from watson.pipeline.frames import PIPELINE_VERSION


@dataclass(frozen=True)
class MemoryClaim:
    id: str
    date: str
    kind: str
    original_assertion: str
    assertion: str
    original_text: str
    text: str
    quotes: tuple[str, ...]
    action: str | None


@dataclass(frozen=True)
class MemoryPage:
    date: str | None
    working_day_id: str | None
    choices: tuple[DayChoice, ...]
    claims: tuple[MemoryClaim, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "working_day_id": self.working_day_id,
            "choices": [{"date": item.date, "state": item.state} for item in self.choices],
            "claims": [
                {
                    "id": item.id,
                    "date": item.date,
                    "kind": item.kind,
                    "original_assertion": item.original_assertion,
                    "assertion": item.assertion,
                    "original_text": item.original_text,
                    "text": item.text,
                    "quotes": list(item.quotes),
                    "action": item.action,
                }
                for item in self.claims
            ],
        }


def collect_memory(
    conn: sqlcipher.Connection,
    *,
    date: str | None = None,
    pipeline_version: int = PIPELINE_VERSION,
) -> MemoryPage:
    """Load one working day's claims with correction overlays."""
    choices = _choices(conn)
    chosen = date if date is not None else (choices[0].date if choices else None)
    if chosen is None:
        return MemoryPage(date=None, working_day_id=None, choices=(), claims=())
    day = conn.execute(
        """
        SELECT id, date FROM working_days WHERE date = ?
        ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        (chosen,),
    ).fetchone()
    if day is None:
        return MemoryPage(date=chosen, working_day_id=None, choices=choices, claims=())
    day_id = str(day["id"])
    return MemoryPage(
        date=str(day["date"]),
        working_day_id=day_id,
        choices=choices,
        claims=_claims(conn, day_id, pipeline_version),
    )


def _claims(
    conn: sqlcipher.Connection, working_day_id: str, pipeline_version: int
) -> tuple[MemoryClaim, ...]:
    rows = conn.execute(
        """
        SELECT c.id AS id, c.kind AS kind, c.assertion AS assertion, c.text AS text,
               d.date AS date
        FROM claims c
        JOIN working_days d ON d.id = c.working_day_id
        WHERE c.working_day_id = ? AND c.pipeline_version = ?
        ORDER BY c.created_at, c.id
        """,
        (working_day_id, pipeline_version),
    ).fetchall()
    quotes = _quotes(conn, working_day_id, pipeline_version)
    histories = claim_histories(
        conn, working_day_id=working_day_id, pipeline_version=pipeline_version
    )
    out: list[MemoryClaim] = []
    for row in rows:
        ident = str(row["id"])
        original_text = str(row["text"])
        original_assertion = str(row["assertion"])
        history = histories.get(ident, ())
        text, assertion, latest = overlay_from_history(original_text, original_assertion, history)
        out.append(
            MemoryClaim(
                id=ident,
                date=str(row["date"]),
                kind=str(row["kind"]),
                original_assertion=original_assertion,
                assertion=assertion,
                original_text=original_text,
                text=text,
                quotes=tuple(quotes.get(ident, ())),
                action=None if latest is None else latest.action,
            )
        )
    return tuple(out)


def _quotes(
    conn: sqlcipher.Connection, working_day_id: str, pipeline_version: int
) -> dict[str, list[str]]:
    rows = conn.execute(
        """
        SELECT e.claim_id AS claim_id, e.snippet AS quote
        FROM claim_evidence e
        JOIN claims c ON c.id = e.claim_id
        WHERE c.working_day_id = ? AND c.pipeline_version = ?
        ORDER BY e.created_at, e.id
        """,
        (working_day_id, pipeline_version),
    ).fetchall()
    out: dict[str, list[str]] = {}
    for row in rows:
        out.setdefault(str(row["claim_id"]), []).append(str(row["quote"]))
    return out


def memory_claim(
    conn: sqlcipher.Connection,
    claim_id: str,
    *,
    pipeline_version: int = PIPELINE_VERSION,
) -> MemoryClaim | None:
    """One claim for ``GET /api/claims?id=``. ``None`` if missing or other version."""
    row = conn.execute(
        """
        SELECT c.id AS id, c.kind AS kind, c.assertion AS assertion, c.text AS text,
               c.working_day_id AS working_day_id, d.date AS date
        FROM claims c
        JOIN working_days d ON d.id = c.working_day_id
        WHERE c.id = ? AND c.pipeline_version = ?
        """,
        (claim_id, pipeline_version),
    ).fetchone()
    if row is None:
        return None
    page = collect_memory(conn, date=str(row["date"]), pipeline_version=pipeline_version)
    for item in page.claims:
        if item.id == claim_id:
            return item
    return None

"""Additive user corrections for claims and entities (plan §4 / D8 / M4b).

Corrections are new rows linked to the claim or entity they address.
Claim text and evidence are never overwritten or deleted. Entity merge
sets ``merged_into_id`` and copies aliases; rename keeps the old name as
an alias. Latest correction per ``(target, pipeline_version)`` wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlcipher3 import dbapi2 as sqlcipher

from watson.intelligence.resolver import normalize_alias
from watson.memory.db import iso_now
from watson.memory.ids import new_id
from watson.pipeline.frames import PIPELINE_VERSION

ClaimAction = Literal["confirm", "correct", "reject", "not_work"]
TargetType = Literal["claim", "entity"]

CLAIM_ACTIONS: frozenset[str] = frozenset({"confirm", "correct", "reject", "not_work"})
EXCLUDED_ACTIONS: frozenset[str] = frozenset({"reject", "not_work"})
ACTION_ASSERTION: dict[str, str] = {
    "confirm": "user_confirmed",
    "correct": "user_corrected",
    "reject": "user_rejected",
    "not_work": "user_rejected",
}
MAX_NEW_TEXT = 2000


class CorrectionError(ValueError):
    """User correction could not be applied."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Correction:
    id: str
    target_type: str
    target_id: str
    action: str
    new_text: str | None
    previous_assertion: str | None
    working_day_id: str | None
    pipeline_version: int
    created_at: str


def record_claim_correction(
    conn: sqlcipher.Connection,
    claim_id: str,
    action: str,
    *,
    new_text: str | None = None,
    pipeline_version: int = PIPELINE_VERSION,
) -> Correction:
    """Insert a claim correction. Never mutates ``claims.text`` or evidence."""
    if action not in CLAIM_ACTIONS:
        raise CorrectionError("action must be confirm, correct, reject, or not_work")
    cleaned = _clean_new_text(new_text)
    if action == "correct" and cleaned is None:
        raise CorrectionError("correct requires new_text")
    if action != "correct":
        cleaned = None
    claim = conn.execute(
        """
        SELECT id, assertion, working_day_id, pipeline_version
        FROM claims
        WHERE id = ? AND pipeline_version = ?
        """,
        (claim_id, pipeline_version),
    ).fetchone()
    if claim is None:
        raise CorrectionError("claim not found", status_code=404)
    correction = _insert(
        conn,
        target_type="claim",
        target_id=str(claim["id"]),
        action=action,
        new_text=cleaned,
        previous_assertion=str(claim["assertion"]),
        working_day_id=str(claim["working_day_id"]),
        pipeline_version=int(claim["pipeline_version"]),
    )
    conn.commit()
    return correction


def rename_entity(
    conn: sqlcipher.Connection,
    entity_id: str,
    new_name: str,
    *,
    pipeline_version: int = PIPELINE_VERSION,
) -> Correction:
    """Rename an entity. The previous canonical name is kept as an alias."""
    cleaned = _clean_new_text(new_name)
    if cleaned is None:
        raise CorrectionError("rename requires new_text")
    entity = _load_entity(conn, entity_id)
    old = str(entity["canonical_name"])
    if normalize_alias(old) == normalize_alias(cleaned):
        raise CorrectionError("rename is unchanged")
    clash = conn.execute(
        """
        SELECT id FROM entities
        WHERE type = ? AND canonical_name = ? AND merged_into_id IS NULL AND id != ?
        """,
        (str(entity["type"]), cleaned, entity_id),
    ).fetchone()
    if clash is not None:
        raise CorrectionError("name already exists")
    stamp = iso_now()
    _ensure_user_alias(conn, entity_id, old, stamp)
    conn.execute(
        """
        UPDATE entities
        SET canonical_name = ?, updated_at = ?, provisional = 0
        WHERE id = ?
        """,
        (cleaned, stamp, entity_id),
    )
    correction = _insert(
        conn,
        target_type="entity",
        target_id=entity_id,
        action="correct",
        new_text=cleaned,
        previous_assertion=None,
        working_day_id=None,
        pipeline_version=pipeline_version,
        stamp=stamp,
    )
    conn.commit()
    return correction


def merge_entities(
    conn: sqlcipher.Connection,
    source_id: str,
    dest_id: str,
    *,
    pipeline_version: int = PIPELINE_VERSION,
) -> Correction:
    """Merge ``source_id`` into ``dest_id``. Source row is kept, not deleted."""
    if source_id == dest_id:
        raise CorrectionError("cannot merge an entity into itself")
    source = _load_entity(conn, source_id)
    dest = _load_entity(conn, dest_id)
    if str(source["type"]) != str(dest["type"]):
        raise CorrectionError("entities must be the same type")
    stamp = iso_now()
    _ensure_user_alias(conn, dest_id, str(source["canonical_name"]), stamp)
    for row in conn.execute("SELECT alias FROM entity_aliases WHERE entity_id = ?", (source_id,)):
        _ensure_user_alias(conn, dest_id, str(row["alias"]), stamp)
    conn.execute(
        """
        UPDATE entities
        SET merged_into_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (dest_id, stamp, source_id),
    )
    correction = _insert(
        conn,
        target_type="entity",
        target_id=source_id,
        action="merge",
        new_text=dest_id,
        previous_assertion=None,
        working_day_id=None,
        pipeline_version=pipeline_version,
        stamp=stamp,
    )
    conn.commit()
    return correction


def claim_histories(
    conn: sqlcipher.Connection,
    *,
    working_day_id: str | None = None,
    pipeline_version: int = PIPELINE_VERSION,
) -> dict[str, tuple[Correction, ...]]:
    """All claim corrections in order, scoped by day and pipeline version."""
    sql = """
        SELECT id, target_type, target_id, action, new_text, previous_assertion,
               working_day_id, pipeline_version, created_at
        FROM corrections
        WHERE target_type = 'claim' AND pipeline_version = ?
        """
    params: list[object] = [pipeline_version]
    if working_day_id is not None:
        sql += " AND working_day_id = ?"
        params.append(working_day_id)
    sql += " ORDER BY created_at, rowid"
    grouped: dict[str, list[Correction]] = {}
    for row in conn.execute(sql, params):
        item = _from_row(row)
        grouped.setdefault(item.target_id, []).append(item)
    return {key: tuple(values) for key, values in grouped.items()}


def overlay_from_history(
    original_text: str,
    original_assertion: str,
    history: tuple[Correction, ...],
) -> tuple[str, str, Correction | None]:
    """Walk the full correction list: latest action, last corrected wording."""
    if not history:
        return original_text, original_assertion, None
    text = original_text
    assertion = original_assertion
    for item in history:
        if item.action == "correct" and item.new_text:
            text = item.new_text
        assertion = ACTION_ASSERTION.get(item.action, assertion)
    return text, assertion, history[-1]


def apply_claim_corrections(
    conn: sqlcipher.Connection,
    claims: list[dict[str, Any]],
    *,
    working_day_id: str,
    pipeline_version: int = PIPELINE_VERSION,
    exclude_rejected: bool = True,
) -> list[dict[str, Any]]:
    """Overlay corrected text/assertion. Optionally drop reject/not_work."""
    histories = claim_histories(
        conn, working_day_id=working_day_id, pipeline_version=pipeline_version
    )
    out: list[dict[str, Any]] = []
    for row in claims:
        history = histories.get(str(row["id"]), ())
        text, assertion, latest = overlay_from_history(
            str(row["text"]), str(row["assertion"]), history
        )
        if exclude_rejected and latest is not None and latest.action in EXCLUDED_ACTIONS:
            continue
        overlay = dict(row)
        overlay["text"] = text
        overlay["assertion"] = assertion
        out.append(overlay)
    return out


def rejected_claim_ids(
    conn: sqlcipher.Connection,
    *,
    working_day_id: str,
    pipeline_version: int = PIPELINE_VERSION,
) -> frozenset[str]:
    histories = claim_histories(
        conn, working_day_id=working_day_id, pipeline_version=pipeline_version
    )
    skipped: set[str] = set()
    for claim_id, history in histories.items():
        if history and history[-1].action in EXCLUDED_ACTIONS:
            skipped.add(claim_id)
    return frozenset(skipped)


def _load_entity(conn: sqlcipher.Connection, entity_id: str) -> Any:
    row = conn.execute(
        """
        SELECT id, type, canonical_name, merged_into_id
        FROM entities WHERE id = ?
        """,
        (entity_id,),
    ).fetchone()
    if row is None or row["merged_into_id"] is not None:
        raise CorrectionError("entity not found", status_code=404)
    return row


def _ensure_user_alias(conn: sqlcipher.Connection, entity_id: str, alias: str, stamp: str) -> None:
    existing = conn.execute(
        "SELECT id FROM entity_aliases WHERE entity_id = ? AND alias = ?",
        (entity_id, alias),
    ).fetchone()
    if existing is not None:
        return
    conn.execute(
        """
        INSERT INTO entity_aliases (
            id, created_at, updated_at, entity_id, alias, source
        ) VALUES (?, ?, ?, ?, ?, 'user')
        """,
        (new_id(), stamp, stamp, entity_id, alias),
    )


def _clean_new_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) > MAX_NEW_TEXT:
        raise CorrectionError("new_text is too long")
    return cleaned


def _insert(
    conn: sqlcipher.Connection,
    *,
    target_type: str,
    target_id: str,
    action: str,
    new_text: str | None,
    previous_assertion: str | None,
    working_day_id: str | None,
    pipeline_version: int,
    stamp: str | None = None,
) -> Correction:
    moment = stamp or iso_now()
    correction_id = new_id()
    conn.execute(
        """
        INSERT INTO corrections (
            id, created_at, updated_at, target_type, target_id, action, new_text,
            previous_assertion, working_day_id, pipeline_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            correction_id,
            moment,
            moment,
            target_type,
            target_id,
            action,
            new_text,
            previous_assertion,
            working_day_id,
            pipeline_version,
        ),
    )
    return Correction(
        id=correction_id,
        target_type=target_type,
        target_id=target_id,
        action=action,
        new_text=new_text,
        previous_assertion=previous_assertion,
        working_day_id=working_day_id,
        pipeline_version=pipeline_version,
        created_at=moment,
    )


def _from_row(row: Any) -> Correction:
    new_text = row["new_text"]
    previous = row["previous_assertion"]
    day_id = row["working_day_id"]
    return Correction(
        id=str(row["id"]),
        target_type=str(row["target_type"]),
        target_id=str(row["target_id"]),
        action=str(row["action"]),
        new_text=None if new_text is None else str(new_text),
        previous_assertion=None if previous is None else str(previous),
        working_day_id=None if day_id is None else str(day_id),
        pipeline_version=int(row["pipeline_version"]),
        created_at=str(row["created_at"]),
    )

"""Additive user corrections for claims and entities (plan §4 / D8 / M4b).

Corrections are new rows linked to the claim or entity they address.
Claim text and evidence are never overwritten or deleted. Entity merge
sets ``merged_into_id`` and copies aliases; rename keeps the old name as
an alias. Rows are never rewritten. After a ``pipeline_version`` bump,
a correction still applies when the same canonical entity, item key
(kind + folded original text) or unique evidence key still exists.
Otherwise it is orphaned and kept.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Literal

from sqlcipher3 import dbapi2 as sqlcipher

from watson.intelligence.resolver import normalize_alias
from watson.memory.db import iso_now
from watson.memory.ids import new_id
from watson.pipeline.frames import PIPELINE_VERSION

_FOLD = re.compile(r"[^a-z0-9]+")

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
    pipeline_version: int | None = PIPELINE_VERSION,
) -> dict[str, tuple[Correction, ...]]:
    """Claim corrections in order, grouped by recorded ``target_id``.

    ``pipeline_version=None`` loads every generation for the day so a later
    apply can rematch by item or evidence key.
    """
    grouped: dict[str, list[Correction]] = {}
    for item in _claim_corrections(
        conn, working_day_id=working_day_id, pipeline_version=pipeline_version
    ):
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
    _ = pipeline_version
    histories = match_claim_corrections(conn, claims, working_day_id=working_day_id)[0]
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
    claims = _claim_rows(conn, working_day_id, pipeline_version)
    histories = match_claim_corrections(conn, claims, working_day_id=working_day_id)[0]
    skipped: set[str] = set()
    for claim_id, history in histories.items():
        if history and history[-1].action in EXCLUDED_ACTIONS:
            skipped.add(claim_id)
    return frozenset(skipped)


def match_claim_corrections(
    conn: sqlcipher.Connection,
    claims: list[dict[str, Any]],
    *,
    working_day_id: str,
) -> tuple[dict[str, tuple[Correction, ...]], tuple[Correction, ...]]:
    """Map current claims to correction histories; leftover rows are orphans.

    Matching prefers the recorded claim id, then the same item key (kind +
    folded original text), then a unique evidence key on this day. Existing
    correction rows are not updated.
    """
    stored = _claim_corrections(conn, working_day_id=working_day_id, pipeline_version=None)
    if not stored:
        return {str(row["id"]): () for row in claims}, ()
    order = {item.id: index for index, item in enumerate(stored)}
    originals = _claim_identities(conn, {item.target_id for item in stored})
    current = _claim_identities(conn, {str(row["id"]) for row in claims})
    by_target: dict[str, list[Correction]] = defaultdict(list)
    by_item: dict[str, list[Correction]] = defaultdict(list)
    by_evidence: dict[str, list[Correction]] = defaultdict(list)
    for item in stored:
        by_target[item.target_id].append(item)
        ident = originals.get(item.target_id)
        if ident is None:
            continue
        by_item[ident.item_key].append(item)
        if ident.evidence_key is not None:
            by_evidence[ident.evidence_key].append(item)
    current_evidence_counts: dict[str, int] = defaultdict(int)
    for ident in current.values():
        if ident.evidence_key is not None:
            current_evidence_counts[ident.evidence_key] += 1
    original_evidence_counts: dict[str, int] = defaultdict(int)
    for ident in originals.values():
        if ident.evidence_key is not None:
            original_evidence_counts[ident.evidence_key] += 1
    matched: dict[str, tuple[Correction, ...]] = {}
    used: set[str] = set()
    for row in claims:
        claim_id = str(row["id"])
        ident = current.get(claim_id)
        found: dict[str, Correction] = {}
        for item in by_target.get(claim_id, ()):
            found[item.id] = item
        if ident is not None:
            for item in by_item.get(ident.item_key, ()):
                found[item.id] = item
            evidence_key = ident.evidence_key
            if (
                evidence_key is not None
                and current_evidence_counts[evidence_key] == 1
                and original_evidence_counts[evidence_key] == 1
            ):
                for item in by_evidence.get(evidence_key, ()):
                    found[item.id] = item
        history = tuple(sorted(found.values(), key=lambda item: order[item.id]))
        matched[claim_id] = history
        used.update(item.id for item in history)
    orphans = tuple(item for item in stored if item.id not in used)
    return matched, orphans


def orphaned_corrections(
    conn: sqlcipher.Connection,
    *,
    working_day_id: str,
    pipeline_version: int = PIPELINE_VERSION,
) -> tuple[Correction, ...]:
    """Corrections whose target is gone from the current generation.

    Claim rows are rematched across versions. Entity rows are orphaned only
    when the canonical entity id is missing. Nothing is deleted.
    """
    claims = _claim_rows(conn, working_day_id, pipeline_version)
    claim_orphans = match_claim_corrections(conn, claims, working_day_id=working_day_id)[1]
    return claim_orphans + _orphaned_entity_corrections(conn)


@dataclass(frozen=True)
class _ClaimIdentity:
    item_key: str
    evidence_key: str | None


def _fold(value: str) -> str:
    return " ".join(_FOLD.sub(" ", value.casefold()).split())


def _item_key(kind: str, text: str) -> str:
    return f"item:{kind}:{_fold(text)}"


def _evidence_key(kind: str, snippets: tuple[str, ...]) -> str | None:
    folded = tuple(sorted({_fold(snippet) for snippet in snippets if _fold(snippet)}))
    if not folded:
        return None
    return f"evidence:{kind}:" + "|".join(folded)


def _claim_corrections(
    conn: sqlcipher.Connection,
    *,
    working_day_id: str | None,
    pipeline_version: int | None,
) -> tuple[Correction, ...]:
    sql = """
        SELECT id, target_type, target_id, action, new_text, previous_assertion,
               working_day_id, pipeline_version, created_at
        FROM corrections
        WHERE target_type = 'claim'
        """
    params: list[object] = []
    if pipeline_version is not None:
        sql += " AND pipeline_version = ?"
        params.append(pipeline_version)
    if working_day_id is not None:
        sql += " AND working_day_id = ?"
        params.append(working_day_id)
    sql += " ORDER BY created_at, rowid"
    return tuple(_from_row(row) for row in conn.execute(sql, params))


def _claim_rows(
    conn: sqlcipher.Connection, working_day_id: str, pipeline_version: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, kind, assertion, text
        FROM claims
        WHERE working_day_id = ? AND pipeline_version = ?
        ORDER BY created_at, id
        """,
        (working_day_id, pipeline_version),
    ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "kind": str(row["kind"]),
            "assertion": str(row["assertion"]),
            "text": str(row["text"]),
        }
        for row in rows
    ]


def _claim_identities(conn: sqlcipher.Connection, claim_ids: set[str]) -> dict[str, _ClaimIdentity]:
    if not claim_ids:
        return {}
    placeholders = ",".join("?" for _ in claim_ids)
    claims = conn.execute(
        f"SELECT id, kind, text FROM claims WHERE id IN ({placeholders})",
        tuple(claim_ids),
    ).fetchall()
    snippets: dict[str, list[str]] = {str(row["id"]): [] for row in claims}
    evidence = conn.execute(
        f"SELECT claim_id, snippet FROM claim_evidence WHERE claim_id IN ({placeholders})",
        tuple(claim_ids),
    ).fetchall()
    for row in evidence:
        snippets.setdefault(str(row["claim_id"]), []).append(str(row["snippet"]))
    out: dict[str, _ClaimIdentity] = {}
    for row in claims:
        claim_id = str(row["id"])
        kind = str(row["kind"])
        out[claim_id] = _ClaimIdentity(
            item_key=_item_key(kind, str(row["text"])),
            evidence_key=_evidence_key(kind, tuple(snippets.get(claim_id, ()))),
        )
    return out


def _orphaned_entity_corrections(conn: sqlcipher.Connection) -> tuple[Correction, ...]:
    rows = conn.execute(
        """
        SELECT c.id, c.target_type, c.target_id, c.action, c.new_text,
               c.previous_assertion, c.working_day_id, c.pipeline_version, c.created_at
        FROM corrections c
        WHERE c.target_type = 'entity'
          AND NOT EXISTS (SELECT 1 FROM entities e WHERE e.id = c.target_id)
        ORDER BY c.created_at, c.rowid
        """
    ).fetchall()
    return tuple(_from_row(row) for row in rows)


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

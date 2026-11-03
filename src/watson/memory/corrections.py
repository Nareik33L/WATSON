"""Additive user corrections for claims and entities (plan §4 / D8 / M4b).

Corrections are new rows linked to the claim or entity they address.
Claim text and evidence are never overwritten or deleted. Entity merge
sets ``merged_into_id`` and copies aliases; rename keeps the old name as
an alias. Rows are never rewritten.

Within the same ``pipeline_version``, overlay is by ``target_id`` only.
Across versions, rematch only when exactly one old-generation claim and
exactly one current claim share the item key or evidence key (keys include
project/person context). Anything ambiguous is orphaned and kept.
"""

from __future__ import annotations

import json
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
    histories = match_claim_corrections(
        conn, claims, working_day_id=working_day_id, pipeline_version=pipeline_version
    )[0]
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
    histories = match_claim_corrections(
        conn, claims, working_day_id=working_day_id, pipeline_version=pipeline_version
    )[0]
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
    pipeline_version: int = PIPELINE_VERSION,
) -> tuple[dict[str, tuple[Correction, ...]], tuple[Correction, ...]]:
    """Map current claims to correction histories; leftover rows are orphans.

    Same-version rows match ``target_id`` only. Older rows rematch only on a
    1:1 item or evidence key, counted across every recoverable claim in that
    old generation and every current claim. Existing rows are not updated.
    """
    stored = _claim_corrections(conn, working_day_id=working_day_id, pipeline_version=None)
    empty: dict[str, tuple[Correction, ...]] = {str(row["id"]): () for row in claims}
    if not stored:
        return empty, ()
    order = {item.id: index for index, item in enumerate(stored)}
    current_ids = {str(row["id"]) for row in claims}
    versions = {pipeline_version} | {
        item.pipeline_version for item in stored if item.pipeline_version < pipeline_version
    }
    generations = {
        version: _generation_identities(conn, working_day_id, version) for version in versions
    }
    item_index = {
        version: _key_index(idents, "item_key") for version, idents in generations.items()
    }
    evidence_index = {
        version: _key_index(idents, "evidence_key") for version, idents in generations.items()
    }
    attached: dict[str, dict[str, Correction]] = {claim_id: {} for claim_id in current_ids}
    used: set[str] = set()
    for item in stored:
        target = _attach_correction(
            item,
            current_ids=current_ids,
            current_version=pipeline_version,
            generations=generations,
            item_index=item_index,
            evidence_index=evidence_index,
        )
        if target is None or target not in attached:
            continue
        attached[target][item.id] = item
        used.add(item.id)
    matched = {
        claim_id: tuple(sorted(found.values(), key=lambda row: order[row.id]))
        for claim_id, found in attached.items()
    }
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
    claim_orphans = match_claim_corrections(
        conn, claims, working_day_id=working_day_id, pipeline_version=pipeline_version
    )[1]
    return claim_orphans + _orphaned_entity_corrections(conn)


@dataclass(frozen=True)
class _ClaimIdentity:
    item_key: str
    evidence_key: str | None


def _fold(value: str) -> str:
    return " ".join(_FOLD.sub(" ", value.casefold()).split())


def _item_key(context: str, kind: str, text: str) -> str:
    return f"item:{context}:{kind}:{_fold(text)}"


def _evidence_key(context: str, kind: str, snippets: tuple[str, ...]) -> str | None:
    folded = tuple(sorted({_fold(snippet) for snippet in snippets if _fold(snippet)}))
    if not folded:
        return None
    return f"evidence:{context}:{kind}:" + "|".join(folded)


def _generation_identities(
    conn: sqlcipher.Connection, working_day_id: str, pipeline_version: int
) -> dict[str, _ClaimIdentity]:
    rows = conn.execute(
        """
        SELECT id FROM claims
        WHERE working_day_id = ? AND pipeline_version = ?
        """,
        (working_day_id, pipeline_version),
    ).fetchall()
    return _claim_identities(conn, {str(row["id"]) for row in rows})


def _key_index(idents: dict[str, _ClaimIdentity], field: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for claim_id, ident in idents.items():
        key = ident.item_key if field == "item_key" else ident.evidence_key
        if key:
            grouped[key].append(claim_id)
    return grouped


def _unique_claim(index: dict[str, list[str]], key: str | None) -> str | None:
    if key is None:
        return None
    hits = index.get(key, ())
    if len(hits) != 1:
        return None
    return hits[0]


def _attach_correction(
    item: Correction,
    *,
    current_ids: set[str],
    current_version: int,
    generations: dict[int, dict[str, _ClaimIdentity]],
    item_index: dict[int, dict[str, list[str]]],
    evidence_index: dict[int, dict[str, list[str]]],
) -> str | None:
    if item.pipeline_version >= current_version:
        return item.target_id if item.target_id in current_ids else None
    old = generations.get(item.pipeline_version, {})
    ident = old.get(item.target_id)
    if ident is None:
        return None
    candidates: set[str] = set()
    item_hit = _unique_claim(item_index[item.pipeline_version], ident.item_key)
    current_item = _unique_claim(item_index[current_version], ident.item_key)
    if item_hit == item.target_id and current_item is not None:
        candidates.add(current_item)
    evidence_hit = _unique_claim(evidence_index[item.pipeline_version], ident.evidence_key)
    current_evidence = _unique_claim(evidence_index[current_version], ident.evidence_key)
    if evidence_hit == item.target_id and current_evidence is not None:
        candidates.add(current_evidence)
    if len(candidates) != 1:
        return None
    return next(iter(candidates))


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
        f"""
        SELECT c.id AS id, c.kind AS kind, c.text AS text, o.raw_json AS raw_json
        FROM claims c
        JOIN observations o ON o.id = c.observation_id
        WHERE c.id IN ({placeholders})
        """,
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
        context = _context_key(conn, str(row["raw_json"] or ""))
        out[claim_id] = _ClaimIdentity(
            item_key=_item_key(context, kind, str(row["text"])),
            evidence_key=_evidence_key(context, kind, tuple(snippets.get(claim_id, ()))),
        )
    return out


def _context_key(conn: sqlcipher.Connection, raw_json: str) -> str:
    try:
        payload = json.loads(raw_json or "{}")
    except json.JSONDecodeError:
        return "none"
    if not isinstance(payload, dict):
        return "none"
    parts: list[str] = []
    for typ, field in (("project", "project_candidates"), ("person", "people_candidates")):
        names = payload.get(field) or []
        if not isinstance(names, list):
            continue
        for name in names:
            if not isinstance(name, str) or not name.strip():
                continue
            resolved = _resolve_entity(conn, name, typ)
            parts.append(resolved or f"{typ}:{normalize_alias(name)}")
    return ",".join(sorted(parts)) if parts else "none"


def _resolve_entity(conn: sqlcipher.Connection, name: str, typ: str) -> str | None:
    needle = normalize_alias(name)
    if not needle:
        return None
    row = conn.execute(
        """
        SELECT e.id
        FROM entities e
        LEFT JOIN entity_aliases a ON a.entity_id = e.id
        WHERE e.type = ?
          AND e.merged_into_id IS NULL
          AND (lower(e.canonical_name) = ? OR lower(a.alias) = ?)
        ORDER BY e.provisional ASC, e.canonical_name, e.id
        LIMIT 1
        """,
        (typ, needle, needle),
    ).fetchone()
    return None if row is None else str(row["id"])


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

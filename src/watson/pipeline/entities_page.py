"""Projects and People pages: entities, aliases, linked claims (plan §8 / M4).

Read-only. Links claims via observation candidates and entity_history;
never exposes merged text. Evidence quotes are tier-3 snippets already
stored on claims.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from sqlcipher3 import dbapi2 as sqlcipher

from watson.intelligence.resolver import normalize_alias
from watson.pipeline.frames import PIPELINE_VERSION

EntityType = Literal["project", "person"]
HEADINGS = {"project": "Projects", "person": "People"}


@dataclass(frozen=True)
class EntityListItem:
    id: str
    name: str
    provisional: bool
    aliases: tuple[str, ...]
    claim_count: int


@dataclass(frozen=True)
class EntityClaimView:
    id: str
    kind: str
    assertion: str
    text: str
    quotes: tuple[str, ...]
    date: str | None


@dataclass(frozen=True)
class EntityDeltaView:
    date: str
    text: str


@dataclass(frozen=True)
class _ObservationCandidates:
    """Parsed observation candidates. JSON is loaded once per page."""

    id: str
    projects: frozenset[str]
    people: frozenset[str]


@dataclass(frozen=True)
class EntityMergeTarget:
    id: str
    name: str


@dataclass(frozen=True)
class EntityDetail:
    id: str
    name: str
    entity_type: str
    description: str | None
    provisional: bool
    aliases: tuple[str, ...]
    claims: tuple[EntityClaimView, ...]
    deltas: tuple[EntityDeltaView, ...]
    merge_targets: tuple[EntityMergeTarget, ...] = ()


@dataclass(frozen=True)
class EntityPage:
    entity_type: str
    heading: str
    items: tuple[EntityListItem, ...]
    selected: EntityDetail | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "heading": self.heading,
            "items": [
                {
                    "id": item.id,
                    "name": item.name,
                    "provisional": item.provisional,
                    "aliases": list(item.aliases),
                    "claim_count": item.claim_count,
                }
                for item in self.items
            ],
            "selected": None
            if self.selected is None
            else {
                "id": self.selected.id,
                "name": self.selected.name,
                "entity_type": self.selected.entity_type,
                "description": self.selected.description,
                "provisional": self.selected.provisional,
                "aliases": list(self.selected.aliases),
                "claims": [
                    {
                        "id": claim.id,
                        "kind": claim.kind,
                        "assertion": claim.assertion,
                        "text": claim.text,
                        "quotes": list(claim.quotes),
                        "date": claim.date,
                    }
                    for claim in self.selected.claims
                ],
                "deltas": [
                    {"date": delta.date, "text": delta.text} for delta in self.selected.deltas
                ],
                "merge_targets": [
                    {"id": item.id, "name": item.name} for item in self.selected.merge_targets
                ],
            },
        }


def collect_entities(
    conn: sqlcipher.Connection,
    *,
    entity_type: EntityType,
    entity_id: str | None = None,
    pipeline_version: int = PIPELINE_VERSION,
) -> EntityPage:
    """Load projects or people, optionally one entity's claims and aliases."""
    heading = HEADINGS[entity_type]
    aliases_by_id = _aliases(conn)
    observations = _observation_candidates(conn, pipeline_version)
    rows = conn.execute(
        """
        SELECT id, canonical_name, description, provisional
        FROM entities
        WHERE type = ? AND merged_into_id IS NULL
        ORDER BY canonical_name COLLATE NOCASE, id
        """,
        (entity_type,),
    ).fetchall()
    details: dict[str, EntityDetail] = {}
    items: list[EntityListItem] = []
    for row in rows:
        ident = str(row["id"])
        names = (str(row["canonical_name"]), *aliases_by_id.get(ident, ()))
        detail = _detail(
            conn,
            ident,
            name=str(row["canonical_name"]),
            entity_type=entity_type,
            description=None if row["description"] is None else str(row["description"]),
            provisional=bool(row["provisional"]),
            aliases=aliases_by_id.get(ident, ()),
            names=names,
            pipeline_version=pipeline_version,
            observations=observations,
        )
        details[ident] = detail
        items.append(
            EntityListItem(
                id=ident,
                name=detail.name,
                provisional=detail.provisional,
                aliases=detail.aliases,
                claim_count=len(detail.claims),
            )
        )
    selected = details.get(entity_id) if entity_id else None
    if selected is not None:
        targets = tuple(
            EntityMergeTarget(id=item.id, name=item.name)
            for item in items
            if item.id != selected.id
        )
        selected = EntityDetail(
            id=selected.id,
            name=selected.name,
            entity_type=selected.entity_type,
            description=selected.description,
            provisional=selected.provisional,
            aliases=selected.aliases,
            claims=selected.claims,
            deltas=selected.deltas,
            merge_targets=targets,
        )
    return EntityPage(
        entity_type=entity_type,
        heading=heading,
        items=tuple(items),
        selected=selected,
    )


def _aliases(conn: sqlcipher.Connection) -> dict[str, tuple[str, ...]]:
    rows = conn.execute(
        """
        SELECT entity_id, alias
        FROM entity_aliases
        ORDER BY alias COLLATE NOCASE, id
        """
    ).fetchall()
    out: dict[str, list[str]] = {}
    for row in rows:
        out.setdefault(str(row["entity_id"]), []).append(str(row["alias"]))
    return {key: tuple(values) for key, values in out.items()}


def _observation_candidates(
    conn: sqlcipher.Connection, pipeline_version: int
) -> tuple[_ObservationCandidates, ...]:
    rows = conn.execute(
        "SELECT id, raw_json FROM observations WHERE pipeline_version = ?",
        (pipeline_version,),
    ).fetchall()
    out: list[_ObservationCandidates] = []
    for row in rows:
        try:
            payload = json.loads(row["raw_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        out.append(
            _ObservationCandidates(
                id=str(row["id"]),
                projects=_candidate_names(payload, "project_candidates"),
                people=_candidate_names(payload, "people_candidates"),
            )
        )
    return tuple(out)


def _candidate_names(payload: dict[str, Any], key: str) -> frozenset[str]:
    raw = payload.get(key) or []
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(normalize_alias(str(name)) for name in raw if name)


def _detail(
    conn: sqlcipher.Connection,
    entity_id: str,
    *,
    name: str,
    entity_type: str,
    description: str | None,
    provisional: bool,
    aliases: tuple[str, ...],
    names: tuple[str, ...],
    pipeline_version: int,
    observations: tuple[_ObservationCandidates, ...],
) -> EntityDetail:
    claim_ids = _linked_claim_ids(
        conn, entity_id, entity_type, names, pipeline_version, observations
    )
    return EntityDetail(
        id=entity_id,
        name=name,
        entity_type=entity_type,
        description=description,
        provisional=provisional,
        aliases=aliases,
        claims=_claims(conn, claim_ids, pipeline_version),
        deltas=_deltas(conn, entity_id, pipeline_version),
    )


def _linked_claim_ids(
    conn: sqlcipher.Connection,
    entity_id: str,
    entity_type: str,
    names: tuple[str, ...],
    pipeline_version: int,
    observations: tuple[_ObservationCandidates, ...],
) -> tuple[str, ...]:
    wanted = {normalize_alias(name) for name in names if name}
    ordered: list[str] = []
    seen: set[str] = set()

    def add(claim_id: str) -> None:
        if claim_id and claim_id not in seen:
            seen.add(claim_id)
            ordered.append(claim_id)

    history = conn.execute(
        """
        SELECT claim_ids FROM entity_history
        WHERE entity_id = ? AND pipeline_version = ?
        """,
        (entity_id, pipeline_version),
    ).fetchall()
    for row in history:
        loaded = json.loads(row["claim_ids"])
        if isinstance(loaded, list):
            for item in loaded:
                add(str(item))

    matching: list[str] = []
    for item in observations:
        nameset = item.projects if entity_type == "project" else item.people
        if nameset & wanted:
            matching.append(item.id)
    if matching:
        placeholders = ",".join("?" * len(matching))
        for row in conn.execute(
            f"""
            SELECT id FROM claims
            WHERE observation_id IN ({placeholders}) AND pipeline_version = ?
            ORDER BY created_at, id
            """,
            (*matching, pipeline_version),
        ):
            add(str(row["id"]))
    return tuple(ordered)


def _claims(
    conn: sqlcipher.Connection, claim_ids: tuple[str, ...], pipeline_version: int
) -> tuple[EntityClaimView, ...]:
    if not claim_ids:
        return ()
    placeholders = ",".join("?" * len(claim_ids))
    rows = conn.execute(
        f"""
        SELECT c.id AS id, c.kind AS kind, c.assertion AS assertion, c.text AS text,
               d.date AS date
        FROM claims c
        JOIN working_days d ON d.id = c.working_day_id
        WHERE c.id IN ({placeholders}) AND c.pipeline_version = ?
        ORDER BY d.date DESC, c.created_at, c.id
        """,
        (*claim_ids, pipeline_version),
    ).fetchall()
    quotes: dict[str, list[str]] = {}
    evidence = conn.execute(
        f"""
        SELECT claim_id, snippet FROM claim_evidence
        WHERE claim_id IN ({placeholders})
        ORDER BY created_at, id
        """,
        claim_ids,
    ).fetchall()
    for row in evidence:
        quotes.setdefault(str(row["claim_id"]), []).append(str(row["snippet"]))
    by_id = {str(row["id"]): row for row in rows}
    # Keep history-first order from claim_ids, then append the rest.
    ordered_ids = [item for item in claim_ids if item in by_id]
    leftover = [key for key in by_id if key not in set(ordered_ids)]
    return tuple(
        EntityClaimView(
            id=ident,
            kind=str(by_id[ident]["kind"]),
            assertion=str(by_id[ident]["assertion"]),
            text=str(by_id[ident]["text"]),
            quotes=tuple(quotes.get(ident, ())),
            date=None if by_id[ident]["date"] is None else str(by_id[ident]["date"]),
        )
        for ident in (*ordered_ids, *leftover)
    )


def _deltas(
    conn: sqlcipher.Connection, entity_id: str, pipeline_version: int
) -> tuple[EntityDeltaView, ...]:
    rows = conn.execute(
        """
        SELECT d.date AS date, h.delta_text AS text
        FROM entity_history h
        JOIN working_days d ON d.id = h.working_day_id
        WHERE h.entity_id = ? AND h.pipeline_version = ?
        ORDER BY d.date DESC, h.created_at DESC
        """,
        (entity_id, pipeline_version),
    ).fetchall()
    return tuple(EntityDeltaView(date=str(row["date"]), text=str(row["text"])) for row in rows)

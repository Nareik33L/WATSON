"""Map free-text project/people candidates to entities (plan §3.5).

Order: normalised exact match on ``entity_aliases`` / canonical name, then
embedding cosine through ``EmbeddingRuntime`` above a configurable threshold.
Otherwise create a new ``provisional`` entity. The model never emits DB ids.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlcipher3 import dbapi2 as sqlcipher

from watson.config.schema import DEFAULT_ENTITY_SIMILARITY
from watson.intelligence.runtime.types import EmbeddingRuntime
from watson.memory.db import iso_now
from watson.memory.ids import new_id

EntityType = Literal["project", "person"]
ALIAS_SOURCE_MODEL = "model"


@dataclass(frozen=True)
class ResolvedEntity:
    entity_id: str
    type: EntityType
    canonical_name: str
    alias: str
    provisional: bool
    created: bool
    merged_alias: bool


def normalize_alias(value: str) -> str:
    """Case-fold and collapse whitespace for exact alias matching."""
    return " ".join(value.casefold().split())


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    norm_l = 0.0
    norm_r = 0.0
    for a, b in zip(left, right, strict=True):
        dot += a * b
        norm_l += a * a
        norm_r += b * b
    if norm_l <= 0.0 or norm_r <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_l) * math.sqrt(norm_r))


def resolve_candidates(
    conn: sqlcipher.Connection,
    embedding: EmbeddingRuntime,
    *,
    project_candidates: Sequence[str],
    people_candidates: Sequence[str],
    threshold: float = DEFAULT_ENTITY_SIMILARITY,
    seen_at: str | None = None,
) -> list[ResolvedEntity]:
    """Resolve free-text candidates. Idempotent on repeated identical names."""
    stamp = seen_at or iso_now()
    resolved: list[ResolvedEntity] = []
    seen: set[tuple[EntityType, str]] = set()
    seen_ids: set[str] = set()
    for raw in project_candidates:
        item = _resolve_one(
            conn, embedding, raw, "project", threshold=threshold, seen_at=stamp, seen=seen
        )
        if item is not None and item.entity_id not in seen_ids:
            seen_ids.add(item.entity_id)
            resolved.append(item)
    for raw in people_candidates:
        item = _resolve_one(
            conn, embedding, raw, "person", threshold=threshold, seen_at=stamp, seen=seen
        )
        if item is not None and item.entity_id not in seen_ids:
            seen_ids.add(item.entity_id)
            resolved.append(item)
    return resolved


def _resolve_one(
    conn: sqlcipher.Connection,
    embedding: EmbeddingRuntime,
    raw: str,
    entity_type: EntityType,
    *,
    threshold: float,
    seen_at: str,
    seen: set[tuple[EntityType, str]],
) -> ResolvedEntity | None:
    name = raw.strip()
    key = normalize_alias(name)
    if not name or not key:
        return None
    dedupe = (entity_type, key)
    if dedupe in seen:
        return None
    seen.add(dedupe)

    existing = _exact_match(conn, entity_type, key)
    if existing is not None:
        _touch(conn, existing[0], seen_at)
        merged = _ensure_alias(conn, existing[0], name, seen_at)
        return ResolvedEntity(
            entity_id=existing[0],
            type=entity_type,
            canonical_name=existing[1],
            alias=name,
            provisional=existing[2],
            created=False,
            merged_alias=merged,
        )

    similar = _embedding_match(conn, embedding, name, entity_type, threshold)
    if similar is not None:
        _touch(conn, similar[0], seen_at)
        merged = _ensure_alias(conn, similar[0], name, seen_at)
        return ResolvedEntity(
            entity_id=similar[0],
            type=entity_type,
            canonical_name=similar[1],
            alias=name,
            provisional=similar[2],
            created=False,
            merged_alias=merged,
        )
    return _create_provisional(conn, entity_type, name, seen_at)


def _exact_match(
    conn: sqlcipher.Connection, entity_type: EntityType, key: str
) -> tuple[str, str, bool] | None:
    for row in conn.execute(
        """
        SELECT id, canonical_name, provisional
        FROM entities
        WHERE type = ? AND merged_into_id IS NULL
        """,
        (entity_type,),
    ):
        if normalize_alias(str(row["canonical_name"])) == key:
            return str(row["id"]), str(row["canonical_name"]), int(row["provisional"]) == 1
    for row in conn.execute(
        """
        SELECT e.id, e.canonical_name, e.provisional, a.alias
        FROM entity_aliases a
        JOIN entities e ON e.id = a.entity_id
        WHERE e.type = ? AND e.merged_into_id IS NULL
        """,
        (entity_type,),
    ):
        if normalize_alias(str(row["alias"])) == key:
            return str(row["id"]), str(row["canonical_name"]), int(row["provisional"]) == 1
    return None


def _embedding_match(
    conn: sqlcipher.Connection,
    embedding: EmbeddingRuntime,
    candidate: str,
    entity_type: EntityType,
    threshold: float,
) -> tuple[str, str, bool] | None:
    catalog: list[tuple[str, str, bool, str]] = []
    for row in conn.execute(
        """
        SELECT id, canonical_name, provisional
        FROM entities
        WHERE type = ? AND merged_into_id IS NULL
        ORDER BY canonical_name, id
        """,
        (entity_type,),
    ):
        catalog.append(
            (
                str(row["id"]),
                str(row["canonical_name"]),
                int(row["provisional"]) == 1,
                str(row["canonical_name"]),
            )
        )
    for row in conn.execute(
        """
        SELECT e.id, e.canonical_name, e.provisional, a.alias
        FROM entity_aliases a
        JOIN entities e ON e.id = a.entity_id
        WHERE e.type = ? AND e.merged_into_id IS NULL
        ORDER BY e.canonical_name, a.alias, e.id
        """,
        (entity_type,),
    ):
        catalog.append(
            (
                str(row["id"]),
                str(row["canonical_name"]),
                int(row["provisional"]) == 1,
                str(row["alias"]),
            )
        )
    if not catalog:
        return None
    texts = [candidate, *[item[3] for item in catalog]]
    try:
        vectors = embedding.embed(texts)
    except Exception:
        return None
    if len(vectors) != len(texts) or not vectors[0]:
        return None
    best: tuple[float, tuple[str, str, bool]] | None = None
    for index, item in enumerate(catalog, start=1):
        if index >= len(vectors):
            break
        score = cosine_similarity(vectors[0], vectors[index])
        if score >= threshold and (best is None or score > best[0]):
            best = (score, (item[0], item[1], item[2]))
    return None if best is None else best[1]


def _create_provisional(
    conn: sqlcipher.Connection, entity_type: EntityType, name: str, seen_at: str
) -> ResolvedEntity:
    entity_id = new_id()
    conn.execute(
        """
        INSERT INTO entities (
            id, created_at, updated_at, type, canonical_name, description,
            first_seen, last_seen, provisional, merged_into_id
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, 1, NULL)
        """,
        (entity_id, seen_at, seen_at, entity_type, name, seen_at, seen_at),
    )
    _ensure_alias(conn, entity_id, name, seen_at)
    return ResolvedEntity(
        entity_id=entity_id,
        type=entity_type,
        canonical_name=name,
        alias=name,
        provisional=True,
        created=True,
        merged_alias=False,
    )


def _ensure_alias(conn: sqlcipher.Connection, entity_id: str, alias: str, seen_at: str) -> bool:
    row = conn.execute(
        "SELECT id FROM entity_aliases WHERE entity_id = ? AND alias = ?",
        (entity_id, alias),
    ).fetchone()
    if row is not None:
        return False
    conn.execute(
        """
        INSERT INTO entity_aliases (
            id, created_at, updated_at, entity_id, alias, source
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (new_id(), seen_at, seen_at, entity_id, alias, ALIAS_SOURCE_MODEL),
    )
    return True


def _touch(conn: sqlcipher.Connection, entity_id: str, seen_at: str) -> None:
    conn.execute(
        """
        UPDATE entities
        SET last_seen = ?, updated_at = ?
        WHERE id = ?
        """,
        (seen_at, seen_at, entity_id),
    )

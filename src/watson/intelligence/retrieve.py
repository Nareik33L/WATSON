"""FTS5 retrieval of project/people memory for the understanding prompt.

Vector search is later (plan §7.3). This pass is keyword-only over
``entities.canonical_name`` and ``entity_aliases.alias``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlcipher3 import dbapi2 as sqlcipher

from watson.config.schema import DEFAULT_RETRIEVE_TOP_K

_TOKEN = re.compile(r"[A-Za-z0-9_]{2,}")
_FTS_TABLE = "fts_entity_ctx"


@dataclass(frozen=True)
class RetrievedEntity:
    entity_id: str
    type: str
    name: str
    aliases: tuple[str, ...]


def retrieve_entity_context(
    conn: sqlcipher.Connection,
    query: str,
    *,
    top_k: int = DEFAULT_RETRIEVE_TOP_K,
) -> list[RetrievedEntity]:
    """Return up to ``top_k`` entities whose names/aliases match ``query``."""
    if top_k < 1:
        return []
    match = _fts_query(query)
    if not match:
        return []
    rows = conn.execute(
        "SELECT id, type, canonical_name FROM entities WHERE merged_into_id IS NULL"
    ).fetchall()
    if not rows:
        return []
    aliases: dict[str, list[str]] = {}
    for alias_row in conn.execute(
        """
        SELECT a.entity_id, a.alias
        FROM entity_aliases a
        JOIN entities e ON e.id = a.entity_id
        WHERE e.merged_into_id IS NULL
        """
    ):
        aliases.setdefault(str(alias_row["entity_id"]), []).append(str(alias_row["alias"]))

    conn.execute(f"DROP TABLE IF EXISTS temp.{_FTS_TABLE}")
    conn.execute(
        f"CREATE VIRTUAL TABLE temp.{_FTS_TABLE} USING fts5("
        "name, kind UNINDEXED, entity_id UNINDEXED)"
    )
    for row in rows:
        entity_id = str(row["id"])
        names = [str(row["canonical_name"]), *aliases.get(entity_id, ())]
        for name in names:
            if name.strip():
                conn.execute(
                    f"INSERT INTO temp.{_FTS_TABLE}(name, kind, entity_id) VALUES (?, ?, ?)",
                    (name, str(row["type"]), entity_id),
                )
    try:
        hits = conn.execute(
            f"""
            SELECT entity_id, kind, bm25({_FTS_TABLE}) AS rank
            FROM {_FTS_TABLE}
            WHERE {_FTS_TABLE} MATCH ?
            ORDER BY rank ASC
            """,
            (match,),
        ).fetchall()
    except sqlcipher.OperationalError:
        return []
    finally:
        conn.execute(f"DROP TABLE IF EXISTS temp.{_FTS_TABLE}")

    by_id = {str(row["id"]): row for row in rows}
    out: list[RetrievedEntity] = []
    seen: set[str] = set()
    for hit in hits:
        entity_id = str(hit["entity_id"])
        if entity_id in seen:
            continue
        row = by_id.get(entity_id)
        if row is None:
            continue
        seen.add(entity_id)
        out.append(
            RetrievedEntity(
                entity_id=entity_id,
                type=str(row["type"]),
                name=str(row["canonical_name"]),
                aliases=tuple(aliases.get(entity_id, ())),
            )
        )
        if len(out) >= top_k:
            break
    return out


def format_retrieved_context(rows: list[RetrievedEntity]) -> str:
    if not rows:
        return "No matching projects or people in memory."
    lines = []
    for row in rows:
        extra = f" (aliases: {', '.join(row.aliases)})" if row.aliases else ""
        lines.append(f"- {row.type}: {row.name}{extra}")
    return "\n".join(lines)


def _fts_query(text: str) -> str:
    seen: list[str] = []
    known: set[str] = set()
    for token in _TOKEN.findall(text):
        folded = token.casefold()
        if folded in known:
            continue
        known.add(folded)
        seen.append(folded)
        if len(seen) >= 32:
            break
    return " OR ".join(seen)

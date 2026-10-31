"""Write audit_events with ids/counts/hashes only — never screen content."""

from __future__ import annotations

import json
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.memory.db import iso_now
from watson.memory.ids import new_id

# Keys that would put tier-2 / screen content into the audit trail.
_FORBIDDEN_DETAIL_KEYS = frozenset(
    {
        "text",
        "ocr",
        "window_title",
        "frame_text",
        "merged_text",
        "snippet",
        "description",
    }
)

KIND_INGESTED = "frame_ingested"
KIND_DELETED = "frame_deleted"
KIND_VERIFIED = "frame_deletion_verified"
KIND_REPLAY = "replay_completed"


class AuditError(ValueError):
    """Audit detail included content or an unknown actor."""


def record_audit(
    conn: sqlcipher.Connection,
    *,
    kind: str,
    detail: dict[str, Any],
    actor: str = "system",
    commit: bool = True,
) -> str:
    """Insert one audit row. ``detail`` may hold ids, counts, and hashes only."""
    if actor not in ("system", "user"):
        raise AuditError(f"invalid audit actor {actor!r}")
    leaked = _FORBIDDEN_DETAIL_KEYS.intersection(detail)
    if leaked:
        raise AuditError("audit detail must not include content fields")
    stamp = iso_now()
    event_id = new_id()
    conn.execute(
        """
        INSERT INTO audit_events (id, created_at, updated_at, kind, detail_json, actor)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            stamp,
            stamp,
            kind,
            json.dumps(detail, sort_keys=True),
            actor,
        ),
    )
    if commit:
        conn.commit()
    return event_id

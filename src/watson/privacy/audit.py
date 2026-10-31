"""Write audit_events with ids/counts/hashes only — never screen content."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.memory.db import iso_now
from watson.memory.ids import new_id

KIND_INGESTED = "frame_ingested"
KIND_DELETED = "frame_deleted"
KIND_VERIFIED = "frame_deletion_verified"
KIND_DELETE_FAILED = "frame_deletion_failed"
KIND_SKIPPED = "frame_skipped"
KIND_REPLAY = "replay_completed"

# Normalised keys (casefold, strip, drop ``_`` / ``-`` / space) permitted per kind.
_ALLOWED_DETAIL_KEYS: dict[str, frozenset[str]] = {
    KIND_INGESTED: frozenset({"frameid", "pngsha256"}),
    KIND_DELETED: frozenset({"frameid", "png", "sidecar"}),
    KIND_VERIFIED: frozenset({"frameid"}),
    KIND_DELETE_FAILED: frozenset({"frameid", "error", "reason", "scope"}),
    KIND_SKIPPED: frozenset({"reason"}),
    KIND_REPLAY: frozenset(
        {
            "fixture",
            "framecount",
            "ingested",
            "extracted",
            "deleted",
            "deletionverified",
            "skippedexcluded",
        }
    ),
}

# Content-style stems rejected even if a future kind forgot to list them.
_FORBIDDEN_CONTENT_STEMS = frozenset(
    {
        "text",
        "ocr",
        "ocrtext",
        "content",
        "windowtitle",
        "bundleid",
        "appname",
        "frametext",
        "mergedtext",
        "snippet",
        "description",
        "title",
    }
)


class AuditError(ValueError):
    """Audit detail included content or an unknown actor."""


def normalize_audit_key(key: str) -> str:
    """Casefold, strip, and drop ``_`` / ``-`` / spaces so ``Bundle_ID`` == ``bundleId``."""
    return "".join(char for char in key.strip().casefold() if char not in "_- \t")


def iter_audit_keys(value: object) -> Iterator[str]:
    """Yield every mapping key in ``value``, walking nested dicts and lists."""
    if isinstance(value, dict):
        for raw_key, child in value.items():
            yield str(raw_key)
            yield from iter_audit_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from iter_audit_keys(child)


def _validate_detail(kind: str, detail: dict[str, Any]) -> None:
    allowed = _ALLOWED_DETAIL_KEYS.get(kind)
    if allowed is None:
        raise AuditError(f"unknown audit kind {kind!r}")
    leaked = _forbidden_content_keys(detail)
    if leaked:
        raise AuditError("audit detail must not include content fields")
    extras = {normalize_audit_key(key) for key in iter_audit_keys(detail)} - allowed
    extras.discard("")
    if extras:
        raise AuditError("audit detail includes keys not allowed for this event kind")


def _forbidden_content_keys(detail: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for raw_key in iter_audit_keys(detail):
        normalized = normalize_audit_key(raw_key)
        if normalized in _FORBIDDEN_CONTENT_STEMS or _content_style_key(normalized):
            found.add(raw_key)
    return found


def _content_style_key(normalized: str) -> bool:
    if normalized.endswith("text") or normalized.endswith("content"):
        return True
    if "ocr" in normalized:
        return True
    return False


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
    _validate_detail(kind, detail)
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


def permitted_keys(kind: str) -> frozenset[str]:
    """Normalised keys allowed on ``kind``. Unknown kinds are empty."""
    return _ALLOWED_DETAIL_KEYS.get(kind, frozenset())

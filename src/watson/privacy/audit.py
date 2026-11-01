"""Write audit_events with ids/counts/hashes only — never screen content."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.capture.exclusions import (
    REASON_EXCLUDED_APP,
    REASON_EXCLUDED_TITLE,
    REASON_PRIVATE,
)
from watson.memory.db import iso_now
from watson.memory.ids import new_id

KIND_INGESTED = "frame_ingested"
KIND_DELETED = "frame_deleted"
KIND_VERIFIED = "frame_deletion_verified"
KIND_DELETE_FAILED = "frame_deletion_failed"
KIND_SKIPPED = "frame_skipped"
KIND_DEDUPED = "frame_deduped"
KIND_REPLAY = "replay_completed"
KIND_TIER2_PURGED = "tier2_purged"
KIND_SPOOL_SWEPT = "spool_swept"
KIND_CONSOLIDATED = "day_consolidated"

# Closed sets for enum-like audit values. ``error`` is an error-class code,
# never a free-text exception message or ``type(exc).__name__``.
REASON_VALUES = frozenset({REASON_EXCLUDED_APP, REASON_EXCLUDED_TITLE, REASON_PRIVATE})
SCOPE_EXCLUDED = "excluded"
SCOPE_VALUES = frozenset({SCOPE_EXCLUDED})
ERROR_PERMISSION = "permission"
ERROR_STILL_PRESENT = "still_present"
ERROR_UNLINK_FAILED = "unlink_failed"
ERROR_VALUES = frozenset({ERROR_PERMISSION, ERROR_STILL_PRESENT, ERROR_UNLINK_FAILED})

_SCALAR_TYPES = (str, int, float, bool)
_HEX64 = re.compile(r"[0-9a-fA-F]{64}")
# Lowercase slug, 1–64 chars. Covers day_acme_q4, day_sensitive_traps, day_mini.
_FIXTURE_SLUG = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_UUID_KEY_STEMS = frozenset({"frameid"})
_HEX64_KEY_STEMS = frozenset({"pngsha256"})
_COUNT_KEY_STEMS = frozenset(
    {
        "framecount",
        "ingested",
        "extracted",
        "deleted",
        "deletionverified",
        "skippedexcluded",
        "deduped",
        "segments",
        "distance",
        "threshold",
        "png",
        "sidecar",
        "texts",
        "visuals",
        "orphaned",
        "stale",
        "unprocessed",
        "refused",
        "activities",
        "decisions",
        "commitments",
        "achievements",
        "history",
        "captured",
    }
)

# Normalised keys (casefold, strip, drop ``_`` / ``-`` / space) permitted per kind.
_ALLOWED_DETAIL_KEYS: dict[str, frozenset[str]] = {
    KIND_INGESTED: frozenset({"frameid", "pngsha256"}),
    KIND_DELETED: frozenset({"frameid", "png", "sidecar"}),
    KIND_VERIFIED: frozenset({"frameid"}),
    KIND_DELETE_FAILED: frozenset({"frameid", "error", "reason", "scope"}),
    KIND_SKIPPED: frozenset({"reason"}),
    KIND_DEDUPED: frozenset({"frameid", "previousframeid", "distance", "threshold"}),
    KIND_REPLAY: frozenset(
        {
            "fixture",
            "framecount",
            "ingested",
            "extracted",
            "deleted",
            "deletionverified",
            "skippedexcluded",
            "deduped",
            "segments",
        }
    ),
    KIND_TIER2_PURGED: frozenset({"texts", "visuals", "segments"}),
    KIND_SPOOL_SWEPT: frozenset({"png", "sidecar", "orphaned", "stale", "unprocessed", "refused"}),
    KIND_CONSOLIDATED: frozenset(
        {"workingdayid", "activities", "decisions", "commitments", "achievements", "history"}
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


def audit_error_class(exc: BaseException) -> str:
    """Map an exception to a closed audit error-class code."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, PermissionError):
            return ERROR_PERMISSION
        current = current.__cause__ or current.__context__
    if "still present" in str(exc).casefold():
        return ERROR_STILL_PRESENT
    return ERROR_UNLINK_FAILED


def _validate_detail(kind: str, detail: dict[str, Any]) -> None:
    allowed = _ALLOWED_DETAIL_KEYS.get(kind)
    if allowed is None:
        raise AuditError(f"unknown audit kind {kind!r}")
    _validate_values(detail)
    leaked = _forbidden_content_keys(detail)
    if leaked:
        raise AuditError("audit detail must not include content fields")
    extras = {normalize_audit_key(key) for key in iter_audit_keys(detail)} - allowed
    extras.discard("")
    if extras:
        raise AuditError("audit detail includes keys not allowed for this event kind")


def _validate_values(detail: dict[str, Any]) -> None:
    for key, value in detail.items():
        if not isinstance(value, _SCALAR_TYPES):
            raise AuditError(f"audit detail values must be scalars; {key!r} is not")
        normalized = normalize_audit_key(str(key))
        if normalized == "reason" and value not in REASON_VALUES:
            raise AuditError("invalid audit reason")
        elif normalized == "error" and value not in ERROR_VALUES:
            raise AuditError("invalid audit error")
        elif normalized == "scope" and value not in SCOPE_VALUES:
            raise AuditError("invalid audit scope")
        elif normalized == "fixture":
            if type(value) is not str or _FIXTURE_SLUG.fullmatch(value) is None:
                raise AuditError("invalid audit fixture")
        elif _is_count_key(normalized):
            if type(value) is not int or value < 0:
                raise AuditError(f"audit {key} must be a non-negative int")
        elif _is_id_key(normalized):
            if type(value) is not str:
                raise AuditError(f"audit {key} must be a UUID")
            try:
                uuid.UUID(value)
            except (ValueError, TypeError, AttributeError) as exc:
                raise AuditError(f"audit {key} must be a UUID") from exc
        elif _is_hex_key(normalized):
            if type(value) is not str or _HEX64.fullmatch(value) is None:
                raise AuditError(f"audit {key} must be a 64-char hex digest")


def _is_count_key(normalized: str) -> bool:
    return normalized in _COUNT_KEY_STEMS


def _is_id_key(normalized: str) -> bool:
    return normalized in _UUID_KEY_STEMS or (
        normalized.endswith("id") and normalized not in _FORBIDDEN_CONTENT_STEMS
    )


def _is_hex_key(normalized: str) -> bool:
    return normalized in _HEX64_KEY_STEMS or "sha256" in normalized


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
    try:
        detail_json = json.dumps(detail, sort_keys=True, allow_nan=False)
    except TypeError as exc:
        raise AuditError("audit detail values must be scalars") from exc
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
            detail_json,
            actor,
        ),
    )
    if commit:
        conn.commit()
    return event_id


def permitted_keys(kind: str) -> frozenset[str]:
    """Normalised keys allowed on ``kind``. Unknown kinds are empty."""
    return _ALLOWED_DETAIL_KEYS.get(kind, frozenset())

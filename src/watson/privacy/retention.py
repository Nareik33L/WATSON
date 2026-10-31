"""Age-based tier-2 retention sweep (plan §5).

Deletes only extracted text: ``frame_text`` rows, ``frame_visual`` rows when
that table exists, and ``segments.merged_text``. Frame metadata, segment
rows, membership, jobs, and audit history are outside the tier and are
never removed here.

Conservative window (M3 consolidation has not landed): a row is expired
when ``expires_at <= now``. Extract/segment write ``expires_at`` as
``captured_at + retention.tier2_grace_days`` (plan default 7). Once
consolidation exists, M3 should move ``expires_at`` to
``consolidated_at + grace``; this sweep already honors the stored stamp.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlcipher3 import dbapi2 as sqlcipher

from watson.config.schema import DEFAULT_TIER2_GRACE_DAYS, RetentionConfig
from watson.memory.db import iso_now
from watson.privacy.audit import KIND_TIER2_PURGED, record_audit

log = logging.getLogger("watson.privacy.retention")

# Columns this sweep is allowed to mutate. Anything else is out of tier.
_TIER2_TABLES = frozenset({"frame_text", "frame_visual", "segments"})


@dataclass(frozen=True)
class Tier2SweepResult:
    texts: int
    visuals: int
    segments: int

    @property
    def total(self) -> int:
        return self.texts + self.visuals + self.segments


def sweep_tier2(
    conn: sqlcipher.Connection,
    *,
    now: datetime | None = None,
    retention: RetentionConfig | None = None,
) -> Tier2SweepResult:
    """Delete expired tier-2 text. Idempotent and transactional.

    ``retention`` is accepted so the window is config-driven; the predicate
    is ``expires_at <= now``. ``tier2_grace_days`` is the value extract
    already baked into ``expires_at``.
    """
    if retention is None:
        grace = DEFAULT_TIER2_GRACE_DAYS
    else:
        grace = int(retention.tier2_grace_days)
    if grace < 0:
        raise ValueError("tier2_grace_days must be >= 0")
    cutoff = iso_now(now)
    try:
        texts = _delete_expired_rows(conn, "frame_text", cutoff)
        visuals = (
            _delete_expired_rows(conn, "frame_visual", cutoff)
            if _table_exists(conn, "frame_visual")
            else 0
        )
        segments = _clear_expired_merged_text(conn, cutoff)
        result = Tier2SweepResult(texts=texts, visuals=visuals, segments=segments)
        if result.total:
            record_audit(
                conn,
                kind=KIND_TIER2_PURGED,
                detail={
                    "texts": result.texts,
                    "visuals": result.visuals,
                    "segments": result.segments,
                },
                commit=False,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if result.total:
        log.info(
            "purged tier-2 text",
            extra={
                "texts": result.texts,
                "visuals": result.visuals,
                "segments": result.segments,
            },
        )
    return result


def _delete_expired_rows(conn: sqlcipher.Connection, table: str, cutoff: str) -> int:
    if table not in _TIER2_TABLES:
        raise ValueError(f"refusing to sweep non-tier-2 table {table!r}")
    cursor = conn.execute(
        f"DELETE FROM {table} WHERE expires_at IS NOT NULL AND expires_at <= ?",
        (cutoff,),
    )
    return int(cursor.rowcount)


def _clear_expired_merged_text(conn: sqlcipher.Connection, cutoff: str) -> int:
    stamp = iso_now()
    cursor = conn.execute(
        """
        UPDATE segments
        SET merged_text = '', updated_at = ?
        WHERE expires_at IS NOT NULL
          AND expires_at <= ?
          AND merged_text != ''
        """,
        (stamp, cutoff),
    )
    return int(cursor.rowcount)


def _table_exists(conn: sqlcipher.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None

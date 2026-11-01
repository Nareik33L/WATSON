"""Incremental D3 activity segments (plan §3.4). No LLM.

A segment is a maximal run of consecutive extracted frames that share
``(bundle_id, normalised window title)`` while OCR-text similarity stays
at or above a configurable threshold. ``merged_text`` is an order-preserving
line union of *redacted* ``frame_text`` only, and is capped.

``state`` becomes ``closed`` when the next frame starts a different segment
(window or similarity) or when dwell reaches ``close_after_seconds``
(default 20 minutes). Closing at 20 minutes is eligibility (§3.5), not a
new row — D3's maximal-run rule does not split a same-window session.

Re-running over the same frames is a no-op: each frame id is unique in
``segment_frames``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, timedelta

from sqlcipher3 import dbapi2 as sqlcipher

from watson.config.schema import (
    DEFAULT_MERGED_TEXT_MAX_CHARS,
    DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS,
    DEFAULT_SEGMENT_SIMILARITY,
    DEFAULT_TIER2_GRACE_DAYS,
    SegmentationConfig,
)
from watson.memory.db import iso_now, parse_iso
from watson.memory.ids import new_id
from watson.pipeline.frames import PIPELINE_VERSION

log = logging.getLogger("watson.pipeline.segmentation")

SEGMENT_JOB_KIND = "segment"
SEGMENT_STATES = ("open", "closed")
ASSIGNABLE_FRAME_STATES = ("extracted", "deleted")


class SegmentError(RuntimeError):
    """Segmentation failed."""


@dataclass(frozen=True)
class SegmentConfig:
    similarity_threshold: float = DEFAULT_SEGMENT_SIMILARITY
    close_after_seconds: int = DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS
    merged_text_max_chars: int = DEFAULT_MERGED_TEXT_MAX_CHARS
    tier2_grace_days: int = DEFAULT_TIER2_GRACE_DAYS

    @classmethod
    def from_settings(
        cls,
        settings: SegmentationConfig | None,
        *,
        tier2_grace_days: int | None = None,
    ) -> SegmentConfig:
        grace = DEFAULT_TIER2_GRACE_DAYS if tier2_grace_days is None else int(tier2_grace_days)
        if settings is None:
            return cls(tier2_grace_days=grace)
        return cls(
            similarity_threshold=float(settings.similarity_threshold),
            close_after_seconds=int(settings.close_after_seconds),
            merged_text_max_chars=int(settings.merged_text_max_chars),
            tier2_grace_days=grace,
        )


def normalize_title(title: str | None) -> str:
    """Case-fold and collapse whitespace (D3 window-title key)."""
    return " ".join((title or "").casefold().split())


def word_jaccard(left: str, right: str) -> float:
    """Word-set Jaccard. Empty sides do not split a run (OCR miss / chrome-only)."""
    left_words = _words(left)
    right_words = _words(right)
    if not left_words or not right_words:
        return 1.0
    union = left_words | right_words
    return len(left_words & right_words) / len(union)


def merge_lines(existing: str, incoming: str, *, cap: int) -> str:
    """Order-preserving union of non-empty lines, truncated at ``cap``."""
    seen: list[str] = []
    known: set[str] = set()
    for line in _iter_lines(existing) + _iter_lines(incoming):
        if line in known:
            continue
        known.add(line)
        seen.append(line)
    text = "\n".join(seen)
    if len(text) <= cap:
        return text
    clipped = text[:cap]
    cut = clipped.rfind("\n")
    if cut > 0:
        return clipped[:cut]
    return clipped


def new_line_count(previous: str, incoming: str) -> int:
    """Lines in ``incoming`` that were not present in ``previous``."""
    prior = set(_iter_lines(previous))
    return sum(1 for line in _iter_lines(incoming) if line not in prior)


def segment_extracted_frames(
    conn: sqlcipher.Connection,
    *,
    working_day_id: str | None = None,
    config: SegmentConfig | None = None,
) -> int:
    """Assign newly extracted frames to segments. Idempotent and incremental.

    Returns the number of frames assigned on this call (0 on a re-run).
    """
    settings = config or SegmentConfig()
    if settings.similarity_threshold < 0 or settings.similarity_threshold > 1:
        raise SegmentError("similarity_threshold must be in [0, 1]")
    if settings.close_after_seconds < 1:
        raise SegmentError("close_after_seconds must be >= 1")
    if settings.merged_text_max_chars < 1:
        raise SegmentError("merged_text_max_chars must be >= 1")

    assigned = 0
    try:
        if working_day_id is None:
            day_ids = [
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT DISTINCT f.working_day_id
                    FROM frames f
                    JOIN frame_text t ON t.frame_id = f.id
                    WHERE f.state IN ('extracted', 'deleted')
                      AND f.working_day_id IS NOT NULL
                      AND f.id NOT IN (SELECT frame_id FROM segment_frames)
                    ORDER BY f.working_day_id
                    """
                )
            ]
        else:
            day_ids = [working_day_id]
        for day_id in day_ids:
            assigned += _segment_day(conn, day_id, settings)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if assigned:
        log.info("segmented frames", extra={"assigned": assigned})
    from watson.pipeline.understanding import enqueue_eligible_understand_jobs

    enqueue_eligible_understand_jobs(
        conn,
        pipeline_version=PIPELINE_VERSION,
        close_after_seconds=settings.close_after_seconds,
    )
    return assigned


def _segment_day(
    conn: sqlcipher.Connection,
    working_day_id: str,
    settings: SegmentConfig,
) -> int:
    rows = conn.execute(
        """
        SELECT
            f.id AS frame_id,
            f.captured_at AS captured_at,
            f.bundle_id AS bundle_id,
            f.window_title AS window_title,
            f.url_domain AS url_domain,
            t.text_redacted AS text_redacted
        FROM frames f
        JOIN frame_text t ON t.frame_id = f.id
        WHERE f.working_day_id = ?
          AND f.state IN ('extracted', 'deleted')
          AND f.id NOT IN (SELECT frame_id FROM segment_frames)
        ORDER BY f.captured_at ASC, f.id ASC
        """,
        (working_day_id,),
    ).fetchall()
    if not rows:
        return 0

    current = _load_latest_segment(conn, working_day_id)
    assigned = 0
    for row in rows:
        frame_id = str(row["frame_id"])
        captured_at = str(row["captured_at"])
        bundle_id = str(row["bundle_id"] or "")
        title_norm = normalize_title(row["window_title"])
        url_domain = None if row["url_domain"] is None else str(row["url_domain"])
        text = str(row["text_redacted"] or "")
        key = (bundle_id, title_norm)
        if current is not None and _continues(current, key, text, settings):
            current = _extend_segment(
                conn,
                current,
                frame_id=frame_id,
                captured_at=captured_at,
                url_domain=url_domain,
                text=text,
                settings=settings,
            )
        else:
            if current is not None:
                _close_segment(conn, str(current["id"]))
            current = _start_segment(
                conn,
                working_day_id=working_day_id,
                frame_id=frame_id,
                captured_at=captured_at,
                bundle_id=bundle_id,
                title_norm=title_norm,
                url_domain=url_domain,
                text=text,
                settings=settings,
            )
        assigned += 1
    if current is not None:
        _maybe_close_for_dwell(conn, current, settings)
    return assigned


def _continues(
    current: dict[str, str | int],
    key: tuple[str, str],
    text: str,
    settings: SegmentConfig,
) -> bool:
    current_key = (str(current["bundle_id"]), str(current["window_title_norm"]))
    if current_key != key:
        return False
    previous = str(current["last_text"])
    merged = str(current["merged_text"])
    similarity = max(word_jaccard(text, previous), word_jaccard(text, merged))
    return similarity >= settings.similarity_threshold


def _start_segment(
    conn: sqlcipher.Connection,
    *,
    working_day_id: str,
    frame_id: str,
    captured_at: str,
    bundle_id: str,
    title_norm: str,
    url_domain: str | None,
    text: str,
    settings: SegmentConfig,
) -> dict[str, str | int]:
    stamp = iso_now()
    segment_id = new_id()
    merged = merge_lines("", text, cap=settings.merged_text_max_chars)
    expires_at = _expires_at(captured_at, settings.tier2_grace_days)
    conn.execute(
        """
        INSERT INTO segments (
            id, created_at, updated_at, working_day_id, bundle_id, window_title_norm,
            url_domain, started_at, ended_at, frame_count, dwell_seconds, line_changes,
            merged_text, state, expires_at, pipeline_version, start_frame_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0, ?, 'open', ?, ?, ?)
        """,
        (
            segment_id,
            stamp,
            stamp,
            working_day_id,
            bundle_id,
            title_norm,
            url_domain,
            captured_at,
            captured_at,
            merged,
            expires_at,
            PIPELINE_VERSION,
            frame_id,
        ),
    )
    _insert_membership(conn, segment_id, frame_id, captured_at, stamp)
    log.info("opened segment", extra={"segment_id": segment_id, "frame_count": 1})
    return {
        "id": segment_id,
        "bundle_id": bundle_id,
        "window_title_norm": title_norm,
        "started_at": captured_at,
        "ended_at": captured_at,
        "frame_count": 1,
        "line_changes": 0,
        "merged_text": merged,
        "last_text": text,
        "state": "open",
    }


def _extend_segment(
    conn: sqlcipher.Connection,
    current: dict[str, str | int],
    *,
    frame_id: str,
    captured_at: str,
    url_domain: str | None,
    text: str,
    settings: SegmentConfig,
) -> dict[str, str | int]:
    stamp = iso_now()
    started = parse_iso(str(current["started_at"]))
    ended = parse_iso(captured_at)
    dwell = max(0, int((ended - started).total_seconds()))
    added = new_line_count(str(current["last_text"]), text)
    line_changes = int(current["line_changes"]) + added
    merged = merge_lines(str(current["merged_text"]), text, cap=settings.merged_text_max_chars)
    frame_count = int(current["frame_count"]) + 1
    conn.execute(
        """
        UPDATE segments
        SET ended_at = ?,
            frame_count = ?,
            dwell_seconds = ?,
            line_changes = ?,
            merged_text = ?,
            url_domain = COALESCE(url_domain, ?),
            updated_at = ?
        WHERE id = ?
        """,
        (
            captured_at,
            frame_count,
            dwell,
            line_changes,
            merged,
            url_domain,
            stamp,
            current["id"],
        ),
    )
    _insert_membership(conn, str(current["id"]), frame_id, captured_at, stamp)
    log.info(
        "extended segment",
        extra={
            "segment_id": current["id"],
            "frame_count": frame_count,
            "line_changes": line_changes,
            "dwell_seconds": dwell,
        },
    )
    updated = dict(current)
    updated.update(
        {
            "ended_at": captured_at,
            "frame_count": frame_count,
            "line_changes": line_changes,
            "merged_text": merged,
            "last_text": text,
        }
    )
    return updated


def _close_segment(conn: sqlcipher.Connection, segment_id: str) -> None:
    stamp = iso_now()
    conn.execute(
        """
        UPDATE segments
        SET state = 'closed', updated_at = ?
        WHERE id = ? AND state = 'open'
        """,
        (stamp, segment_id),
    )
    log.info("closed segment", extra={"segment_id": segment_id})


def _maybe_close_for_dwell(
    conn: sqlcipher.Connection,
    current: dict[str, str | int],
    settings: SegmentConfig,
) -> None:
    started = parse_iso(str(current["started_at"]))
    ended = parse_iso(str(current["ended_at"]))
    dwell = max(0, int((ended - started).total_seconds()))
    if dwell >= settings.close_after_seconds and str(current["state"]) != "closed":
        _close_segment(conn, str(current["id"]))


def _load_latest_segment(
    conn: sqlcipher.Connection, working_day_id: str
) -> dict[str, str | int] | None:
    row = conn.execute(
        """
        SELECT
            s.id, s.bundle_id, s.window_title_norm, s.started_at, s.ended_at,
            s.frame_count, s.line_changes, s.merged_text, s.state,
            t.text_redacted AS last_text
        FROM segments s
        JOIN segment_frames sf ON sf.segment_id = s.id
        JOIN frame_text t ON t.frame_id = sf.frame_id
        WHERE s.working_day_id = ?
          AND sf.captured_at = s.ended_at
        ORDER BY s.ended_at DESC, s.started_at DESC, s.id DESC
        LIMIT 1
        """,
        (working_day_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "bundle_id": str(row["bundle_id"]),
        "window_title_norm": str(row["window_title_norm"]),
        "started_at": str(row["started_at"]),
        "ended_at": str(row["ended_at"]),
        "frame_count": int(row["frame_count"]),
        "line_changes": int(row["line_changes"]),
        "merged_text": str(row["merged_text"]),
        "last_text": str(row["last_text"] or ""),
        "state": str(row["state"]),
    }


def _insert_membership(
    conn: sqlcipher.Connection,
    segment_id: str,
    frame_id: str,
    captured_at: str,
    stamp: str,
) -> None:
    conn.execute(
        """
        INSERT INTO segment_frames (
            id, created_at, updated_at, segment_id, frame_id, captured_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (new_id(), stamp, stamp, segment_id, frame_id, captured_at),
    )


def _expires_at(captured_at: str, grace_days: int) -> str:
    moment = parse_iso(captured_at).astimezone(UTC)
    return iso_now(moment + timedelta(days=int(grace_days)))


def _iter_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _words(text: str) -> set[str]:
    return {token for token in text.casefold().split() if token}

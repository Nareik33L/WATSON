"""Today timeline: segments, observations, and the day's summary (plan §8 / M4).

No merged text, OCR, or other tier-2 fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.pipeline.day_page import DayPage, collect_day_page
from watson.pipeline.deal_groups import DealGroups
from watson.pipeline.frames import PIPELINE_VERSION


@dataclass(frozen=True)
class TodayObservation:
    """LLM observation for a segment. Work activity only, never merged text."""

    activity_type: str
    description: str
    confidence: float


@dataclass(frozen=True)
class TodaySegment:
    """One activity segment for the Today page. No OCR / merged text."""

    id: str
    app_name: str | None
    window_title: str | None
    started_at: str
    ended_at: str
    dwell_seconds: int
    frame_count: int
    observation: TodayObservation | None = None

    def duration_label(self) -> str:
        hours, rem = divmod(max(0, self.dwell_seconds), 3600)
        minutes, seconds = divmod(rem, 60)
        if hours:
            return f"{hours}h {minutes:02d}m"
        if minutes:
            return f"{minutes}m"
        return f"{seconds}s"

    def range_label(self) -> str:
        return f"{_hhmm(self.started_at)}–{_hhmm(self.ended_at)}"


@dataclass(frozen=True)
class TodayReport:
    date: str | None
    segments: tuple[TodaySegment, ...]
    state: str = "none"
    day: DayPage | None = None
    groups: DealGroups = DealGroups()

    def as_dict(self) -> dict[str, Any]:
        segments: list[dict[str, Any]] = []
        for item in self.segments:
            row: dict[str, Any] = {
                "id": item.id,
                "app_name": item.app_name,
                "window_title": item.window_title,
                "started_at": item.started_at,
                "ended_at": item.ended_at,
                "dwell_seconds": item.dwell_seconds,
                "frame_count": item.frame_count,
            }
            if item.observation is not None:
                row["observation"] = {
                    "activity_type": item.observation.activity_type,
                    "description": item.observation.description,
                    "confidence": item.observation.confidence,
                }
            segments.append(row)
        payload = {
            "date": self.date,
            "state": self.state,
            "segments": segments,
            "summary_present": bool(self.day is not None and self.day.spans),
        }
        payload.update(self.groups.as_dict())
        return payload


def collect_today(
    conn: sqlcipher.Connection,
    *,
    date: str | None = None,
    pipeline_version: int = PIPELINE_VERSION,
) -> TodayReport:
    """Load the current working day: segments, observations, daily summary."""
    chosen = date if date is not None else _latest_day(conn)
    if chosen is None:
        return TodayReport(date=None, segments=(), state="none", day=None)
    rows = conn.execute(
        """
        SELECT
            s.id AS id,
            s.started_at AS started_at,
            s.ended_at AS ended_at,
            s.dwell_seconds AS dwell_seconds,
            s.frame_count AS frame_count,
            f.app_name AS app_name,
            f.window_title AS window_title,
            o.activity_type AS activity_type,
            o.description AS description,
            o.confidence AS confidence
        FROM segments s
        JOIN working_days d ON d.id = s.working_day_id
        LEFT JOIN frames f ON f.id = s.start_frame_id
        LEFT JOIN observations o
            ON o.segment_id = s.id AND o.pipeline_version = ?
        WHERE d.date = ?
          AND (f.state IS NULL OR f.state != 'skipped')
        ORDER BY s.started_at ASC, s.id ASC
        """,
        (pipeline_version, chosen),
    ).fetchall()
    segments = tuple(_segment_from_row(row) for row in rows)
    day = collect_day_page(conn, date=chosen, pipeline_version=pipeline_version)
    return TodayReport(date=chosen, segments=segments, state=day.state, day=day, groups=day.groups)


def _segment_from_row(row: Any) -> TodaySegment:
    observation = None
    if row["activity_type"] is not None and row["description"] is not None:
        observation = TodayObservation(
            activity_type=str(row["activity_type"]),
            description=str(row["description"]),
            confidence=float(row["confidence"] or 0.0),
        )
    return TodaySegment(
        id=str(row["id"]),
        app_name=None if row["app_name"] is None else str(row["app_name"]),
        window_title=None if row["window_title"] is None else str(row["window_title"]),
        started_at=str(row["started_at"]),
        ended_at=str(row["ended_at"]),
        dwell_seconds=int(row["dwell_seconds"]),
        frame_count=int(row["frame_count"]),
        observation=observation,
    )


def _latest_day(conn: sqlcipher.Connection) -> str | None:
    row = conn.execute(
        """
        SELECT d.date
        FROM working_days d
        WHERE EXISTS (
            SELECT 1 FROM segments s WHERE s.working_day_id = d.id
        )
        ORDER BY d.date DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        empty = conn.execute("SELECT date FROM working_days ORDER BY date DESC LIMIT 1").fetchone()
        return None if empty is None else str(empty[0])
    return str(row[0])


def _hhmm(stamp: str) -> str:
    if "T" in stamp and len(stamp) >= 16:
        return stamp[11:16]
    return stamp

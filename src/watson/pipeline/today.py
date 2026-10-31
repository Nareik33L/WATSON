"""Today timeline: segments only (plan §8 / M2). No summaries, no merged text."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "segments": [asdict(item) for item in self.segments],
        }


def collect_today(conn: sqlcipher.Connection, *, date: str | None = None) -> TodayReport:
    """Load segments for ``date``, or the latest working day that has any."""
    chosen = date if date is not None else _latest_day(conn)
    if chosen is None:
        return TodayReport(date=None, segments=())
    rows = conn.execute(
        """
        SELECT
            s.id AS id,
            s.started_at AS started_at,
            s.ended_at AS ended_at,
            s.dwell_seconds AS dwell_seconds,
            s.frame_count AS frame_count,
            f.app_name AS app_name,
            f.window_title AS window_title
        FROM segments s
        JOIN working_days d ON d.id = s.working_day_id
        LEFT JOIN frames f ON f.id = s.start_frame_id
        WHERE d.date = ?
        ORDER BY s.started_at ASC, s.id ASC
        """,
        (chosen,),
    ).fetchall()
    segments = tuple(
        TodaySegment(
            id=str(row["id"]),
            app_name=None if row["app_name"] is None else str(row["app_name"]),
            window_title=None if row["window_title"] is None else str(row["window_title"]),
            started_at=str(row["started_at"]),
            ended_at=str(row["ended_at"]),
            dwell_seconds=int(row["dwell_seconds"]),
            frame_count=int(row["frame_count"]),
        )
        for row in rows
    )
    return TodayReport(date=chosen, segments=segments)


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

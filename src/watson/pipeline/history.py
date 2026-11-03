"""History page: working days with state, linking through to /days (plan §8 / M4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlcipher3 import dbapi2 as sqlcipher

from watson.pipeline.frames import PIPELINE_VERSION


@dataclass(frozen=True)
class HistoryDay:
    date: str
    state: str
    error: str | None
    observation_count: int
    has_summary: bool


@dataclass(frozen=True)
class HistoryPage:
    days: tuple[HistoryDay, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "days": [
                {
                    "date": item.date,
                    "state": item.state,
                    "error": item.error,
                    "observation_count": item.observation_count,
                    "has_summary": item.has_summary,
                }
                for item in self.days
            ]
        }


def collect_history(
    conn: sqlcipher.Connection, *, pipeline_version: int = PIPELINE_VERSION
) -> HistoryPage:
    """List every working day. No merged text."""
    rows = conn.execute(
        """
        SELECT
            d.date AS date,
            d.state AS state,
            d.consolidation_error AS error,
            (
                SELECT COUNT(*)
                FROM observations o
                JOIN segments s ON s.id = o.segment_id
                WHERE s.working_day_id = d.id AND o.pipeline_version = ?
            ) AS observation_count,
            (
                SELECT COUNT(*)
                FROM daily_summaries sm
                WHERE sm.working_day_id = d.id AND sm.pipeline_version = ?
            ) AS summary_count
        FROM working_days d
        ORDER BY d.date DESC, d.created_at DESC
        """,
        (pipeline_version, pipeline_version),
    ).fetchall()
    days = tuple(
        HistoryDay(
            date=str(row["date"]),
            state=str(row["state"]),
            error=None if row["error"] is None else str(row["error"]),
            observation_count=int(row["observation_count"]),
            has_summary=int(row["summary_count"]) > 0,
        )
        for row in rows
    )
    return HistoryPage(days=days)

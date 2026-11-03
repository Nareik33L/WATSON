"""M3f deal clusters: day-scoped deals and segment.deal_id (plan §3.7 / D8).

Revision ID: 0011_m3f_deals
Revises: 0010_m4b_corrections

A segment with ``deal_id`` NULL is the ``none`` class. Re-running
consolidation replaces ``deals`` rows; ON DELETE SET NULL clears stale
assignments before the new clusters are written.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_m3f_deals"
down_revision: str | None = "0010_m4b_corrections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE deals (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            working_day_id TEXT NOT NULL REFERENCES working_days(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            observation_ids TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            pipeline_version INTEGER NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_deals_day ON deals(working_day_id, pipeline_version)")
    op.execute(
        "ALTER TABLE segments ADD COLUMN deal_id TEXT REFERENCES deals(id) ON DELETE SET NULL"
    )
    op.execute("CREATE INDEX idx_segments_deal ON segments(deal_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_segments_deal")
    op.execute("ALTER TABLE segments DROP COLUMN deal_id")
    op.execute("DROP INDEX IF EXISTS idx_deals_day")
    op.execute("DROP TABLE IF EXISTS deals")

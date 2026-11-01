"""M3d daily summaries: narrative with citations (plan §3.7 step 7 / §4).

Revision ID: 0008_m3d_daily_summaries
Revises: 0007_m3c_consolidation
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_m3d_daily_summaries"
down_revision: str | None = "0007_m3c_consolidation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE daily_summaries (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            working_day_id TEXT NOT NULL REFERENCES working_days(id) ON DELETE CASCADE,
            narrative_md TEXT NOT NULL,
            sections_json TEXT NOT NULL,
            claim_ids TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            pipeline_version INTEGER NOT NULL,
            UNIQUE (working_day_id, pipeline_version)
        )
        """
    )
    op.execute("CREATE INDEX idx_daily_summaries_day ON daily_summaries(working_day_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS daily_summaries")

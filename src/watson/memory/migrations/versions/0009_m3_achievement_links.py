"""Link table so one achievement candidate can keep every supporting claim.

Revision ID: 0009_m3_achievement_links
Revises: 0008_m3d_daily_summaries
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_m3_achievement_links"
down_revision: str | None = "0008_m3d_daily_summaries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE achievement_candidate_claims (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            candidate_id TEXT NOT NULL REFERENCES achievement_candidates(id) ON DELETE CASCADE,
            claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            UNIQUE (candidate_id, claim_id),
            UNIQUE (claim_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_achievement_candidate_claims_candidate "
        "ON achievement_candidate_claims(candidate_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS achievement_candidate_claims")

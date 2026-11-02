"""M4b user corrections: additive feedback rows (plan §4 / D8).

Revision ID: 0010_m4b_corrections
Revises: 0009_m3_achievement_links
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0010_m4b_corrections"
down_revision: str | None = "0009_m3_achievement_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DESTRUCTIVE_DOWNGRADE = "WATSON_ALLOW_DESTRUCTIVE_DOWNGRADE"


def upgrade() -> None:
    # Additive only. Claims, evidence, and entities are unchanged.
    op.execute(
        """
        CREATE TABLE corrections (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            action TEXT NOT NULL,
            new_text TEXT,
            previous_assertion TEXT,
            working_day_id TEXT REFERENCES working_days(id) ON DELETE CASCADE,
            pipeline_version INTEGER NOT NULL,
            CHECK (target_type IN ('claim', 'entity')),
            CHECK (action IN (
                'confirm', 'correct', 'reject', 'merge', 'not_work'
            )),
            CHECK (previous_assertion IS NULL OR previous_assertion IN (
                'observed', 'inferred', 'unconfirmed',
                'user_confirmed', 'user_corrected', 'user_rejected'
            ))
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_corrections_target "
        "ON corrections(target_type, target_id, pipeline_version)"
    )
    op.execute("CREATE INDEX idx_corrections_day ON corrections(working_day_id, pipeline_version)")


def downgrade() -> None:
    count = op.get_bind().execute(text("SELECT COUNT(*) FROM corrections")).scalar()
    if int(count or 0) > 0 and os.environ.get(_DESTRUCTIVE_DOWNGRADE) != "1":
        raise RuntimeError(
            "Refusing to downgrade migration 0010: corrections table contains data. "
            "Set WATSON_ALLOW_DESTRUCTIVE_DOWNGRADE=1 to override."
        )
    op.execute("DROP TABLE IF EXISTS corrections")

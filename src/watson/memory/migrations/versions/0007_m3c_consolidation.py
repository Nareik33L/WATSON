"""M3c day consolidation tables: activities, decisions, commitments, history.

Revision ID: 0007_m3c_consolidation
Revises: 0006_m3a_understanding
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_m3c_consolidation"
down_revision: str | None = "0006_m3a_understanding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE working_days ADD COLUMN consolidated_at TEXT")
    op.execute("ALTER TABLE working_days ADD COLUMN consolidation_gap TEXT")
    op.execute(
        """
        CREATE TABLE activities (
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
    op.execute("CREATE INDEX idx_activities_day ON activities(working_day_id)")
    op.execute(
        """
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            working_day_id TEXT NOT NULL REFERENCES working_days(id) ON DELETE CASCADE,
            text TEXT NOT NULL,
            claim_ids TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            pipeline_version INTEGER NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_decisions_day ON decisions(working_day_id)")
    op.execute(
        """
        CREATE TABLE commitments (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            working_day_id TEXT NOT NULL REFERENCES working_days(id) ON DELETE CASCADE,
            text TEXT NOT NULL,
            status TEXT NOT NULL,
            due_hint TEXT,
            claim_ids TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            pipeline_version INTEGER NOT NULL,
            CHECK (status IN ('open', 'done', 'dropped'))
        )
        """
    )
    op.execute("CREATE INDEX idx_commitments_day ON commitments(working_day_id)")
    op.execute(
        """
        CREATE TABLE achievement_candidates (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            working_day_id TEXT NOT NULL REFERENCES working_days(id) ON DELETE CASCADE,
            claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            text TEXT NOT NULL,
            pipeline_version INTEGER NOT NULL,
            UNIQUE (claim_id, pipeline_version)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_achievement_candidates_day ON achievement_candidates(working_day_id)"
    )
    op.execute(
        """
        CREATE TABLE entity_history (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            working_day_id TEXT NOT NULL REFERENCES working_days(id) ON DELETE CASCADE,
            delta_text TEXT NOT NULL,
            claim_ids TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            pipeline_version INTEGER NOT NULL,
            UNIQUE (entity_id, working_day_id, pipeline_version)
        )
        """
    )
    op.execute("CREATE INDEX idx_entity_history_day ON entity_history(working_day_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS entity_history")
    op.execute("DROP TABLE IF EXISTS achievement_candidates")
    op.execute("DROP TABLE IF EXISTS commitments")
    op.execute("DROP TABLE IF EXISTS decisions")
    op.execute("DROP TABLE IF EXISTS activities")
    op.execute("ALTER TABLE working_days DROP COLUMN consolidation_gap")
    op.execute("ALTER TABLE working_days DROP COLUMN consolidated_at")

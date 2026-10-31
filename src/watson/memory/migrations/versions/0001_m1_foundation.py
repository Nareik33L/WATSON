"""M1 foundation tables: frames, jobs, audit_events, working_days, settings.

Revision ID: 0001_m1_foundation
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_m1_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE settings (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            json TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE working_days (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            date TEXT NOT NULL UNIQUE,
            started_at TEXT,
            ended_at TEXT,
            state TEXT NOT NULL,
            consolidation_error TEXT,
            pipeline_version INTEGER NOT NULL,
            CHECK (state IN ('open', 'closing', 'consolidated', 'failed'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE frames (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            working_day_id TEXT REFERENCES working_days(id),
            captured_at TEXT NOT NULL,
            bundle_id TEXT,
            app_name TEXT,
            window_title_hash TEXT,
            window_title TEXT,
            url_domain TEXT,
            phash TEXT,
            state TEXT NOT NULL,
            failure_count INTEGER NOT NULL DEFAULT 0,
            deleted_at TEXT,
            deletion_verified INTEGER NOT NULL DEFAULT 0,
            CHECK (state IN (
                'captured', 'extracted', 'deleted',
                'failed', 'deleted_unprocessed', 'skipped'
            )),
            CHECK (deletion_verified IN (0, 1))
        )
        """
    )
    op.execute("CREATE INDEX idx_frames_state ON frames(state)")
    op.execute("CREATE INDEX idx_frames_working_day ON frames(working_day_id)")
    op.execute(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            run_after TEXT NOT NULL,
            last_error TEXT,
            leased_until TEXT,
            claimed_by TEXT,
            CHECK (state IN ('pending', 'claimed', 'completed', 'failed'))
        )
        """
    )
    op.execute("CREATE INDEX idx_jobs_claim ON jobs(state, run_after)")
    op.execute(
        """
        CREATE TABLE audit_events (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            kind TEXT NOT NULL,
            detail_json TEXT,
            actor TEXT NOT NULL,
            CHECK (actor IN ('system', 'user'))
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_events")
    op.execute("DROP TABLE IF EXISTS jobs")
    op.execute("DROP TABLE IF EXISTS frames")
    op.execute("DROP TABLE IF EXISTS working_days")
    op.execute("DROP TABLE IF EXISTS settings")

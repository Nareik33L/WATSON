"""M2b activity segments (D3 / §3.4).

Revision ID: 0004_m2b_segments
Revises: 0003_m2a_dedup
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_m2b_segments"
down_revision: str | None = "0003_m2a_dedup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE segments (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            working_day_id TEXT NOT NULL REFERENCES working_days(id),
            bundle_id TEXT NOT NULL,
            window_title_norm TEXT NOT NULL,
            url_domain TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            frame_count INTEGER NOT NULL,
            dwell_seconds INTEGER NOT NULL,
            line_changes INTEGER NOT NULL,
            merged_text TEXT NOT NULL,
            state TEXT NOT NULL,
            expires_at TEXT,
            pipeline_version INTEGER NOT NULL,
            start_frame_id TEXT NOT NULL UNIQUE REFERENCES frames(id),
            CHECK (state IN ('open', 'closed')),
            CHECK (frame_count >= 1),
            CHECK (dwell_seconds >= 0),
            CHECK (line_changes >= 0),
            UNIQUE (working_day_id, started_at, bundle_id, window_title_norm)
        )
        """
    )
    op.execute("CREATE INDEX idx_segments_working_day ON segments(working_day_id)")
    op.execute("CREATE INDEX idx_segments_state ON segments(state)")
    op.execute(
        """
        CREATE TABLE segment_frames (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            segment_id TEXT NOT NULL REFERENCES segments(id),
            frame_id TEXT NOT NULL UNIQUE REFERENCES frames(id),
            captured_at TEXT NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_segment_frames_segment ON segment_frames(segment_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS segment_frames")
    op.execute("DROP TABLE IF EXISTS segments")

"""M2c ON DELETE behaviour for tier-2 FKs the retention sweep can touch.

Revision ID: 0005_m2c_on_delete
Revises: 0004_m2b_segments
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_m2c_on_delete"
down_revision: str | None = "0004_m2b_segments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite cannot ALTER an existing FK. Rebuild so deleting a frame or
    # segment cannot leave orphan membership / tier-2 rows (plan §5 / M2c).
    op.execute(
        """
        CREATE TABLE frame_text_new (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            frame_id TEXT NOT NULL UNIQUE REFERENCES frames(id) ON DELETE CASCADE,
            text_redacted TEXT NOT NULL,
            ocr_confidence REAL,
            expires_at TEXT,
            source TEXT NOT NULL
        )
        """
    )
    op.execute("INSERT INTO frame_text_new SELECT * FROM frame_text")
    op.execute("DROP TABLE frame_text")
    op.execute("ALTER TABLE frame_text_new RENAME TO frame_text")
    op.execute("CREATE INDEX idx_frame_text_frame ON frame_text(frame_id)")

    op.execute(
        """
        CREATE TABLE segments_new (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            working_day_id TEXT NOT NULL REFERENCES working_days(id) ON DELETE CASCADE,
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
            start_frame_id TEXT NOT NULL UNIQUE REFERENCES frames(id) ON DELETE CASCADE,
            CHECK (state IN ('open', 'closed')),
            CHECK (frame_count >= 1),
            CHECK (dwell_seconds >= 0),
            CHECK (line_changes >= 0),
            UNIQUE (working_day_id, started_at, bundle_id, window_title_norm)
        )
        """
    )
    op.execute("INSERT INTO segments_new SELECT * FROM segments")
    op.execute(
        """
        CREATE TABLE segment_frames_new (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            segment_id TEXT NOT NULL REFERENCES segments_new(id) ON DELETE CASCADE,
            frame_id TEXT NOT NULL UNIQUE REFERENCES frames(id) ON DELETE CASCADE,
            captured_at TEXT NOT NULL
        )
        """
    )
    op.execute("INSERT INTO segment_frames_new SELECT * FROM segment_frames")
    op.execute("DROP TABLE segment_frames")
    op.execute("DROP TABLE segments")
    op.execute("ALTER TABLE segments_new RENAME TO segments")
    op.execute("ALTER TABLE segment_frames_new RENAME TO segment_frames")
    op.execute("CREATE INDEX idx_segments_working_day ON segments(working_day_id)")
    op.execute("CREATE INDEX idx_segments_state ON segments(state)")
    op.execute("CREATE INDEX idx_segment_frames_segment ON segment_frames(segment_id)")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE frame_text_old (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            frame_id TEXT NOT NULL UNIQUE REFERENCES frames(id),
            text_redacted TEXT NOT NULL,
            ocr_confidence REAL,
            expires_at TEXT,
            source TEXT NOT NULL
        )
        """
    )
    op.execute("INSERT INTO frame_text_old SELECT * FROM frame_text")
    op.execute("DROP TABLE frame_text")
    op.execute("ALTER TABLE frame_text_old RENAME TO frame_text")
    op.execute("CREATE INDEX idx_frame_text_frame ON frame_text(frame_id)")

    op.execute(
        """
        CREATE TABLE segments_old (
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
    op.execute("INSERT INTO segments_old SELECT * FROM segments")
    op.execute(
        """
        CREATE TABLE segment_frames_old (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            segment_id TEXT NOT NULL REFERENCES segments_old(id),
            frame_id TEXT NOT NULL UNIQUE REFERENCES frames(id),
            captured_at TEXT NOT NULL
        )
        """
    )
    op.execute("INSERT INTO segment_frames_old SELECT * FROM segment_frames")
    op.execute("DROP TABLE segment_frames")
    op.execute("DROP TABLE segments")
    op.execute("ALTER TABLE segments_old RENAME TO segments")
    op.execute("ALTER TABLE segment_frames_old RENAME TO segment_frames")
    op.execute("CREATE INDEX idx_segments_working_day ON segments(working_day_id)")
    op.execute("CREATE INDEX idx_segments_state ON segments(state)")
    op.execute("CREATE INDEX idx_segment_frames_segment ON segment_frames(segment_id)")

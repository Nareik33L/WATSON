"""M2a worker-side perceptual-hash columns for consecutive-frame dedup.

Revision ID: 0003_m2a_dedup
Revises: 0002_frame_text
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_m2a_dedup"
down_revision: str | None = "0002_frame_text"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # dhash is the 64-bit gradient hash of the spool PNG. phash (existing)
    # is overwritten at extract with the 64-bit DCT hash of the same PNG.
    # Neither hash can reconstruct pixels (64 bits vs the raw image).
    op.execute("ALTER TABLE frames ADD COLUMN dhash TEXT")
    op.execute("ALTER TABLE frames ADD COLUMN duplicate_of TEXT REFERENCES frames(id)")
    op.execute("ALTER TABLE frames ADD COLUMN duplicate_count INTEGER NOT NULL DEFAULT 0")
    op.execute("CREATE INDEX IF NOT EXISTS idx_frames_duplicate_of ON frames(duplicate_of)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_frames_duplicate_of")
    op.execute("ALTER TABLE frames DROP COLUMN duplicate_count")
    op.execute("ALTER TABLE frames DROP COLUMN duplicate_of")
    op.execute("ALTER TABLE frames DROP COLUMN dhash")

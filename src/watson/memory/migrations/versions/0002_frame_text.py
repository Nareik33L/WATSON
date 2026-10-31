"""M1 frame_text table for the fixture-only extract stub.

Revision ID: 0002_frame_text
Revises: 0001_m1_foundation
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_frame_text"
down_revision: str | None = "0001_m1_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # source='fixture_sidecar' marks M1 replay stand-in OCR. M2 adds live extractors.
    op.execute(
        """
        CREATE TABLE frame_text (
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
    op.execute("CREATE INDEX idx_frame_text_frame ON frame_text(frame_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS frame_text")

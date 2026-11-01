"""M3a understanding tables: observations, claims, evidence, entities.

Revision ID: 0006_m3a_understanding
Revises: 0005_m2c_on_delete
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_m3a_understanding"
down_revision: str | None = "0005_m2c_on_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive only. M2 tables are unchanged (plan §4 / M3a).
    op.execute(
        """
        CREATE TABLE observations (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            segment_id TEXT NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
            activity_type TEXT NOT NULL,
            description TEXT NOT NULL,
            confidence REAL NOT NULL,
            model_id TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            pipeline_version INTEGER NOT NULL,
            raw_json TEXT NOT NULL,
            CHECK (confidence >= 0.0 AND confidence <= 1.0),
            UNIQUE (segment_id, pipeline_version)
        )
        """
    )
    op.execute("CREATE INDEX idx_observations_segment ON observations(segment_id)")
    op.execute(
        """
        CREATE TABLE claims (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            observation_id TEXT NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
            working_day_id TEXT NOT NULL REFERENCES working_days(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            assertion TEXT NOT NULL,
            confidence REAL NOT NULL,
            status TEXT,
            due_hint TEXT,
            pipeline_version INTEGER NOT NULL,
            CHECK (kind IN (
                'activity', 'outcome', 'decision',
                'commitment', 'achievement', 'person_interaction'
            )),
            CHECK (assertion IN (
                'observed', 'inferred', 'unconfirmed',
                'user_confirmed', 'user_corrected', 'user_rejected'
            )),
            CHECK (confidence >= 0.0 AND confidence <= 1.0),
            CHECK (status IS NULL OR status IN ('open', 'done', 'dropped'))
        )
        """
    )
    op.execute("CREATE INDEX idx_claims_observation ON claims(observation_id)")
    op.execute("CREATE INDEX idx_claims_working_day ON claims(working_day_id)")
    op.execute(
        """
        CREATE TABLE claim_evidence (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            segment_id TEXT REFERENCES segments(id) ON DELETE SET NULL,
            frame_id TEXT REFERENCES frames(id) ON DELETE SET NULL,
            snippet TEXT NOT NULL,
            snippet_kind TEXT NOT NULL,
            line_start INTEGER,
            line_end INTEGER,
            CHECK (snippet_kind IN ('ocr', 'visual')),
            CHECK (length(snippet) <= 300)
        )
        """
    )
    op.execute("CREATE INDEX idx_claim_evidence_claim ON claim_evidence(claim_id)")
    op.execute(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            description TEXT,
            first_seen TEXT,
            last_seen TEXT,
            provisional INTEGER NOT NULL DEFAULT 1,
            merged_into_id TEXT REFERENCES entities(id),
            CHECK (type IN ('project', 'person')),
            CHECK (provisional IN (0, 1)),
            UNIQUE (type, canonical_name)
        )
        """
    )
    op.execute("CREATE INDEX idx_entities_type ON entities(type)")
    op.execute(
        """
        CREATE TABLE entity_aliases (
            id TEXT PRIMARY KEY NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            alias TEXT NOT NULL,
            source TEXT NOT NULL,
            UNIQUE (entity_id, alias)
        )
        """
    )
    op.execute("CREATE INDEX idx_entity_aliases_alias ON entity_aliases(alias)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS entity_aliases")
    op.execute("DROP TABLE IF EXISTS entities")
    op.execute("DROP TABLE IF EXISTS claim_evidence")
    op.execute("DROP TABLE IF EXISTS claims")
    op.execute("DROP TABLE IF EXISTS observations")

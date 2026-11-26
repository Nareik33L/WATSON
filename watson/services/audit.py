"""Audit trail for local decisions. Nothing here leaves the machine."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from watson.db.models import AuditRecord


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def record(
    session: Session,
    *,
    organisation_id: str,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    detail: str,
    actor_user_id: str | None = None,
) -> AuditRecord:
    entry = AuditRecord(
        id=new_id("aud"),
        organisation_id=organisation_id,
        actor_user_id=actor_user_id,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        detail=detail,
        created_at=datetime.now(timezone.utc),
    )
    session.add(entry)
    return entry

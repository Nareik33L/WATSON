"""Local approval inbox.

Decisions are stored and audited. This service does not call Outlook, Gong,
HubSpot, Excel, Calendly, or any other system, and it never sends email.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from watson.db.models import Approval
from watson.errors import AppError
from watson.services.audit import record

log = logging.getLogger("watson.approval")

RESULT_NOTE = "Saved locally. No external system was contacted."
_OPEN = frozenset({"pending", "deferred"})


def decide(
    session: Session,
    approval: Approval,
    *,
    action: str,
    note: str,
    proposed_value: str | None,
    actor: str,
    actor_user_id: str | None,
) -> Approval:
    if approval.status not in _OPEN and action != "edit":
        raise AppError(409, "This decision is already recorded. Reset demo data if you want the inbox back.")
    if approval.status not in _OPEN and action == "edit":
        raise AppError(409, "This decision is already recorded and can no longer be edited.")

    cleaned_note = note.strip()
    now = datetime.now(timezone.utc)

    if action == "edit":
        if proposed_value is None or not proposed_value.strip():
            raise AppError(422, "Enter the revised value before saving an edit.")
        approval.proposed_value = proposed_value.strip()
        approval.revision += 1
        approval.decision_note = cleaned_note
        approval.status = "pending"
        detail = f"Edited locally. Revision {approval.revision}. No external system was contacted."
        if cleaned_note:
            detail = f"{detail} Note: {cleaned_note}"
    elif action == "approve":
        approval.status = "approved"
        approval.decided_at = now
        approval.decided_by = actor
        approval.decision_note = cleaned_note
        detail = "Approved locally. Simulated only. No external system was contacted."
        if cleaned_note:
            detail = f"{detail} Note: {cleaned_note}"
    elif action == "reject":
        approval.status = "rejected"
        approval.decided_at = now
        approval.decided_by = actor
        approval.decision_note = cleaned_note
        detail = "Rejected locally. Nothing was sent or changed outside this database."
        if cleaned_note:
            detail = f"{detail} Note: {cleaned_note}"
    elif action == "defer":
        approval.status = "deferred"
        approval.decided_at = now
        approval.decided_by = actor
        approval.decision_note = cleaned_note
        detail = "Deferred locally. No external system was contacted."
        if cleaned_note:
            detail = f"{detail} Note: {cleaned_note}"
    else:
        raise AppError(422, "Choose approve, reject, edit, or defer.")

    record(
        session,
        organisation_id=approval.organisation_id,
        actor=actor,
        actor_user_id=actor_user_id,
        action=action,
        entity_type="approval",
        entity_id=approval.id,
        detail=detail,
    )
    log.info("Approval %s %s locally (simulated, no external call)", approval.id, action)
    return approval

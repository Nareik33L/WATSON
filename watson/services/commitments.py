"""Manual commitment tracking.

Extraction from email and calls is a later phase. Nothing here is marked
complete because a due date has passed.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from watson.db.models import Commitment, Customer, Opportunity, SourceReference
from watson.errors import AppError
from watson.services.audit import new_id, record
from watson.status import effective_status, normalise_stored_status

OWNER_SIDES = {"salesperson", "customer", "stakeholder"}
IMPORTANCE = {"high", "medium", "low"}


def create_commitment(
    session: Session,
    *,
    organisation_id: str,
    customer_id: str,
    opportunity_id: str | None,
    owner_name: str,
    owner_side: str,
    description: str,
    importance: str,
    due_date,
    status: str,
    actor: str,
    actor_user_id: str | None,
) -> Commitment:
    customer = _customer(session, organisation_id, customer_id)
    opportunity = _opportunity(session, customer, opportunity_id)
    _check_owner(owner_side, importance)
    text = description.strip()
    if len(text) < 3:
        raise AppError(422, "Describe the commitment in a few words.")
    stored = _status(status or "New", due_date)
    now = datetime.now(timezone.utc)
    source = SourceReference(
        id=new_id("src"),
        organisation_id=organisation_id,
        customer_id=customer.id,
        opportunity_id=opportunity.id if opportunity else None,
        source_system="manual",
        external_id=f"DEMO-MAN-{new_id('x').split('_', 1)[1]}",
        label="Added in Command Centre",
        occurred_at=now,
        summary=text,
        data_origin="demo",
    )
    session.add(source)
    session.flush()
    commitment = Commitment(
        id=new_id("cmt"),
        organisation_id=organisation_id,
        customer_id=customer.id,
        opportunity_id=opportunity.id if opportunity else None,
        source_reference_id=source.id,
        owner_name=owner_name.strip() or actor,
        owner_side=owner_side,
        description=text,
        importance=importance,
        status=stored,
        due_date=due_date,
        evidence="Added manually in the command centre.",
        related_recommendation="",
        completion_note="",
        created_at=now,
        completed_at=now if stored == "Completed" else None,
        data_origin="demo",
    )
    session.add(commitment)
    record(
        session,
        organisation_id=organisation_id,
        actor=actor,
        actor_user_id=actor_user_id,
        action="create",
        entity_type="commitment",
        entity_id=commitment.id,
        detail=f"Commitment created. Status {effective_status(stored, due_date)}. Not inferred from a source system.",
    )
    session.flush()
    return commitment


def update_commitment(
    session: Session,
    commitment: Commitment,
    *,
    description: str | None,
    owner_name: str | None,
    owner_side: str | None,
    importance: str | None,
    due_date,
    status: str | None,
    note: str | None,
    actor: str,
    actor_user_id: str | None,
) -> Commitment:
    if description is not None:
        text = description.strip()
        if len(text) < 3:
            raise AppError(422, "Describe the commitment in a few words.")
        commitment.description = text
    if owner_name is not None and owner_name.strip():
        commitment.owner_name = owner_name.strip()
    if owner_side is not None:
        if owner_side not in OWNER_SIDES:
            raise AppError(422, "Owner must be the salesperson, the customer, or another stakeholder.")
        commitment.owner_side = owner_side
    if importance is not None:
        if importance not in IMPORTANCE:
            raise AppError(422, "Importance must be high, medium, or low.")
        commitment.importance = importance
    if due_date is not None:
        commitment.due_date = due_date
    if status is not None:
        commitment.status = _status(status, commitment.due_date)
    if commitment.status == "Completed":
        if commitment.completed_at is None:
            commitment.completed_at = datetime.now(timezone.utc)
        if note:
            commitment.completion_note = note.strip()
    else:
        commitment.completed_at = None
        if note and status in {"Cancelled", "Unclear"}:
            commitment.completion_note = note.strip()
    record(
        session,
        organisation_id=commitment.organisation_id,
        actor=actor,
        actor_user_id=actor_user_id,
        action="update",
        entity_type="commitment",
        entity_id=commitment.id,
        detail=(
            f"Commitment updated. Status {effective_status(commitment.status, commitment.due_date)}. "
            "Completion is never inferred from the due date."
        ),
    )
    session.flush()
    return commitment


def _customer(session: Session, organisation_id: str, customer_id: str) -> Customer:
    customer = session.get(Customer, customer_id)
    if customer is None or customer.organisation_id != organisation_id:
        raise AppError(422, "Choose a customer from the demo portfolio.")
    return customer


def _opportunity(session: Session, customer: Customer, opportunity_id: str | None) -> Opportunity | None:
    if not opportunity_id:
        return None
    opportunity = session.get(Opportunity, opportunity_id)
    if opportunity is None or opportunity.customer_id != customer.id:
        raise AppError(422, "That opportunity is not on the selected customer.")
    return opportunity


def _check_owner(owner_side: str, importance: str) -> None:
    if owner_side not in OWNER_SIDES:
        raise AppError(422, "Owner must be the salesperson, the customer, or another stakeholder.")
    if importance not in IMPORTANCE:
        raise AppError(422, "Importance must be high, medium, or low.")


def _status(requested: str, due_date) -> str:
    try:
        return normalise_stored_status(requested, due_date)
    except ValueError as exc:
        raise AppError(422, "Unknown commitment status.") from exc

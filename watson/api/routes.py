"""HTTP routes. They stay thin: read models for pages, services for decisions."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from watson.api.deps import filter_params, get_db
from watson.api.schemas import CommitmentIn, CommitmentPatch, DecisionIn, SettingsPatch
from watson.dal.filters import PortfolioFilter
from watson.db.models import Approval, Commitment
from watson.errors import AppError
from watson.readmodels import (
    activity,
    approval_dict,
    approvals,
    briefing,
    commitment_dict,
    commitments,
    customer_detail,
    customers,
    deal_detail,
    deals,
    files,
    filters,
    forecast,
    health,
    integration_payload,
    integrations,
    job_payload,
    meta,
    overview,
    settings_payload,
)
from watson.seed.demo import reset_demo
from watson.services.actor import current_actor
from watson.services.approval import RESULT_NOTE, decide
from watson.services.audit import record
from watson.services.commitments import create_commitment, update_commitment
from watson.services.integration import save_poll_interval
from watson.services.sync import manual_refresh
from watson.tenant import current_organisation_id
from watson.dal import queries

router = APIRouter()


@router.get("/health")
def get_health(db: Session = Depends(get_db)) -> dict:
    return health(db)


@router.get("/meta")
def get_meta(db: Session = Depends(get_db)) -> dict:
    return meta(db, current_organisation_id())


@router.get("/filters")
def get_filters(db: Session = Depends(get_db)) -> dict:
    return filters(db, current_organisation_id())


@router.get("/overview")
def get_overview(
    portfolio: PortfolioFilter = Depends(filter_params),
    db: Session = Depends(get_db),
) -> dict:
    return overview(db, current_organisation_id(), portfolio)


@router.get("/briefing")
def get_briefing(
    portfolio: PortfolioFilter = Depends(filter_params),
    db: Session = Depends(get_db),
) -> dict:
    return briefing(db, current_organisation_id(), portfolio)


@router.get("/deals")
def get_deals(
    scope: str = "open",
    portfolio: PortfolioFilter = Depends(filter_params),
    db: Session = Depends(get_db),
) -> dict:
    if scope not in {"open", "all"}:
        raise AppError(422, "Scope must be open or all.")
    return deals(db, current_organisation_id(), portfolio, scope)


@router.get("/deals/{deal_id}")
def get_deal(deal_id: str, db: Session = Depends(get_db)) -> dict:
    payload = deal_detail(db, current_organisation_id(), deal_id)
    if payload is None:
        raise AppError(404, "That opportunity is not in the demo portfolio.")
    return payload


@router.get("/customers")
def get_customers(
    portfolio: PortfolioFilter = Depends(filter_params),
    db: Session = Depends(get_db),
) -> dict:
    return customers(db, current_organisation_id(), portfolio)


@router.get("/customers/{customer_id}")
def get_customer(customer_id: str, db: Session = Depends(get_db)) -> dict:
    payload = customer_detail(db, current_organisation_id(), customer_id)
    if payload is None:
        raise AppError(404, "That customer is not in the demo portfolio.")
    return payload


@router.get("/commitments")
def get_commitments(
    status: str | None = None,
    portfolio: PortfolioFilter = Depends(filter_params),
    db: Session = Depends(get_db),
) -> dict:
    return commitments(db, current_organisation_id(), portfolio, status)


@router.post("/commitments", status_code=201)
def post_commitment(body: CommitmentIn, db: Session = Depends(get_db)) -> dict:
    actor_id, actor = current_actor(db)
    created = create_commitment(
        db,
        organisation_id=current_organisation_id(),
        customer_id=body.customer_id,
        opportunity_id=body.opportunity_id or None,
        owner_name=body.owner_name,
        owner_side=body.owner_side,
        description=body.description,
        importance=body.importance,
        due_date=body.due_date,
        status=body.status,
        actor=actor,
        actor_user_id=actor_id,
    )
    return _present_commitment(db, created)


@router.patch("/commitments/{commitment_id}")
def patch_commitment(commitment_id: str, body: CommitmentPatch, db: Session = Depends(get_db)) -> dict:
    commitment = _commitment(db, commitment_id)
    actor_id, actor = current_actor(db)
    update_commitment(
        db,
        commitment,
        description=body.description,
        owner_name=body.owner_name,
        owner_side=body.owner_side,
        importance=body.importance,
        due_date=body.due_date,
        status=body.status,
        note=body.note,
        actor=actor,
        actor_user_id=actor_id,
    )
    return _present_commitment(db, commitment)


@router.get("/approvals")
def get_approvals(
    status: str | None = None,
    portfolio: PortfolioFilter = Depends(filter_params),
    db: Session = Depends(get_db),
) -> dict:
    return approvals(db, current_organisation_id(), portfolio, status)


@router.post("/approvals/{approval_id}/decision")
def post_decision(approval_id: str, body: DecisionIn, db: Session = Depends(get_db)) -> dict:
    approval = _approval(db, approval_id)
    actor_id, actor = current_actor(db)
    decide(
        db,
        approval,
        action=body.action,
        note=body.note,
        proposed_value=body.proposed_value,
        actor=actor,
        actor_user_id=actor_id,
    )
    payload = _present_approval(db, approval)
    payload["result_note"] = RESULT_NOTE
    return payload


@router.get("/forecast")
def get_forecast(
    portfolio: PortfolioFilter = Depends(filter_params),
    db: Session = Depends(get_db),
) -> dict:
    return forecast(db, current_organisation_id(), portfolio)


@router.get("/files")
def get_files(db: Session = Depends(get_db)) -> dict:
    return files(db, current_organisation_id())


@router.get("/activity")
def get_activity(kind: str | None = None, source: str | None = None, db: Session = Depends(get_db)) -> dict:
    return activity(db, current_organisation_id(), kind, source)


@router.get("/integrations")
def get_integrations(db: Session = Depends(get_db)) -> dict:
    return integrations(db, current_organisation_id())


@router.post("/integrations/{key}/refresh")
def post_refresh(key: str, db: Session = Depends(get_db)) -> dict:
    org_id = current_organisation_id()
    integration = queries.get_integration(db, org_id, key)
    if integration is None:
        raise AppError(404, "Unknown integration.")
    actor_id, actor = current_actor(db)
    job = manual_refresh(db, integration, actor=actor, actor_user_id=actor_id)
    return {
        "demo": True,
        "integration": integration_payload(integration, db, org_id),
        "job": job_payload(job, integration),
    }


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)) -> dict:
    return settings_payload(db, current_organisation_id(), _database_name(db))


@router.patch("/settings")
def patch_settings(body: SettingsPatch, db: Session = Depends(get_db)) -> dict:
    org_id = current_organisation_id()
    actor_id, actor = current_actor(db)
    save_poll_interval(db, org_id, body.poll_interval_minutes)
    record(
        db,
        organisation_id=org_id,
        actor=actor,
        actor_user_id=actor_id,
        action="update",
        entity_type="setting",
        entity_id="poll_interval_minutes",
        detail=(
            f"Poll interval stored as {body.poll_interval_minutes} minutes. "
            "Polling is not running."
        ),
    )
    return settings_payload(db, org_id, _database_name(db))


@router.post("/demo/reset")
def post_reset(db: Session = Depends(get_db)) -> dict:
    org_id = current_organisation_id()
    actor_id, actor = current_actor(db)
    reset_demo(db)
    record(
        db,
        organisation_id=org_id,
        actor=actor,
        actor_user_id=actor_id,
        action="reset",
        entity_type="organisation",
        entity_id=org_id,
        detail="Fictional demo portfolio reset. No live data was touched.",
    )
    return {"demo": True, "reset": True}


def _present_commitment(db: Session, commitment: Commitment) -> dict:
    customer = queries.get_customer(db, commitment.organisation_id, commitment.customer_id)
    opportunity = None
    if commitment.opportunity_id:
        from watson.db.models import Opportunity

        opportunity = db.get(Opportunity, commitment.opportunity_id)
    if customer is None:
        raise AppError(404, "Commitment not found.")
    return commitment_dict(commitment, customer, opportunity)


def _present_approval(db: Session, approval: Approval) -> dict:
    customer = None
    opportunity = None
    if approval.customer_id:
        customer = queries.get_customer(db, approval.organisation_id, approval.customer_id)
    if approval.opportunity_id:
        from watson.db.models import Opportunity

        opportunity = db.get(Opportunity, approval.opportunity_id)
    return approval_dict(approval, customer, opportunity)


def _commitment(db: Session, commitment_id: str) -> Commitment:
    commitment = db.get(Commitment, commitment_id)
    if commitment is None or commitment.organisation_id != current_organisation_id():
        raise AppError(404, "Commitment not found.")
    return commitment


def _approval(db: Session, approval_id: str) -> Approval:
    approval = db.get(Approval, approval_id)
    if approval is None or approval.organisation_id != current_organisation_id():
        raise AppError(404, "Approval not found.")
    return approval


def _database_name(db: Session) -> str:
    bind = db.get_bind()
    name = bind.url.database or "command.db"
    return name.rsplit("/", 1)[-1]

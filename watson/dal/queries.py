"""Read queries. Filtering happens here so every view uses the same rules.

Date range applies to opportunity close dates and commitment due dates.
Approvals ignore the date range so a pending decision is not hidden by a
close-date window. Region, business unit, and owner follow the account.
Product, pipeline, stage, and opportunity follow the deal.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from watson.dal.filters import PortfolioFilter
from watson.db.models import (
    Approval,
    AuditRecord,
    Commitment,
    Contact,
    Customer,
    Integration,
    IntegrationConfig,
    IntelligenceRecord,
    Interaction,
    Opportunity,
    OpportunityContact,
    PortfolioChange,
    Setting,
    SourceReference,
    StoredFile,
    SyncJob,
    User,
)


def list_deals(session: Session, org_id: str, filters: PortfolioFilter) -> list[tuple[Opportunity, Customer, User]]:
    statement = (
        select(Opportunity, Customer, User)
        .join(Customer, Opportunity.customer_id == Customer.id)
        .join(User, Opportunity.owner_id == User.id)
        .where(Opportunity.organisation_id == org_id)
    )
    if filters.date_from:
        statement = statement.where(Opportunity.close_date >= filters.date_from)
    if filters.date_to:
        statement = statement.where(Opportunity.close_date <= filters.date_to)
    if filters.owner_id:
        statement = statement.where(Opportunity.owner_id == filters.owner_id)
    if filters.pipeline:
        statement = statement.where(Opportunity.pipeline == filters.pipeline)
    if filters.region:
        statement = statement.where(Customer.region == filters.region)
    if filters.business_unit:
        statement = statement.where(Customer.business_unit == filters.business_unit)
    if filters.product:
        statement = statement.where(Opportunity.product == filters.product)
    if filters.customer_id:
        statement = statement.where(Opportunity.customer_id == filters.customer_id)
    if filters.opportunity_id:
        statement = statement.where(Opportunity.id == filters.opportunity_id)
    if filters.stage:
        statement = statement.where(Opportunity.stage == filters.stage)
    statement = statement.order_by(Opportunity.value.desc())
    return list(session.execute(statement).all())


def get_deal(session: Session, org_id: str, deal_id: str) -> tuple[Opportunity, Customer, User] | None:
    statement = (
        select(Opportunity, Customer, User)
        .join(Customer, Opportunity.customer_id == Customer.id)
        .join(User, Opportunity.owner_id == User.id)
        .where(Opportunity.organisation_id == org_id, Opportunity.id == deal_id)
    )
    return session.execute(statement).first()


def list_customers(session: Session, org_id: str, filters: PortfolioFilter) -> list[Customer]:
    statement = select(Customer).where(Customer.organisation_id == org_id)
    if filters.owner_id:
        statement = statement.where(Customer.owner_id == filters.owner_id)
    if filters.region:
        statement = statement.where(Customer.region == filters.region)
    if filters.business_unit:
        statement = statement.where(Customer.business_unit == filters.business_unit)
    if filters.customer_id:
        statement = statement.where(Customer.id == filters.customer_id)
    statement = statement.order_by(Customer.name)
    return list(session.scalars(statement))


def get_customer(session: Session, org_id: str, customer_id: str) -> Customer | None:
    customer = session.get(Customer, customer_id)
    if customer is None or customer.organisation_id != org_id:
        return None
    return customer


def list_contacts(session: Session, org_id: str, customer_id: str) -> list[Contact]:
    statement = (
        select(Contact)
        .where(Contact.organisation_id == org_id, Contact.customer_id == customer_id)
        .order_by(Contact.active.desc(), Contact.name)
    )
    return list(session.scalars(statement))


def list_stakeholders(session: Session, org_id: str, opportunity_id: str) -> list[tuple[OpportunityContact, Contact]]:
    statement = (
        select(OpportunityContact, Contact)
        .join(Contact, OpportunityContact.contact_id == Contact.id)
        .where(OpportunityContact.organisation_id == org_id, OpportunityContact.opportunity_id == opportunity_id)
        .order_by(OpportunityContact.is_primary.desc(), Contact.name)
    )
    return list(session.execute(statement).all())


def list_commitments(
    session: Session, org_id: str, filters: PortfolioFilter
) -> list[tuple[Commitment, Customer, Opportunity | None]]:
    statement = (
        select(Commitment, Customer, Opportunity)
        .join(Customer, Commitment.customer_id == Customer.id)
        .outerjoin(Opportunity, Commitment.opportunity_id == Opportunity.id)
        .where(Commitment.organisation_id == org_id)
    )
    if filters.date_from:
        statement = statement.where(Commitment.due_date >= filters.date_from)
    if filters.date_to:
        statement = statement.where(Commitment.due_date <= filters.date_to)
    if filters.owner_id:
        statement = statement.where(Customer.owner_id == filters.owner_id)
    if filters.region:
        statement = statement.where(Customer.region == filters.region)
    if filters.business_unit:
        statement = statement.where(Customer.business_unit == filters.business_unit)
    if filters.customer_id:
        statement = statement.where(Commitment.customer_id == filters.customer_id)
    if filters.opportunity_id:
        statement = statement.where(Commitment.opportunity_id == filters.opportunity_id)
    if filters.product:
        statement = statement.where(Opportunity.product == filters.product)
    if filters.pipeline:
        statement = statement.where(Opportunity.pipeline == filters.pipeline)
    if filters.stage:
        statement = statement.where(Opportunity.stage == filters.stage)
    statement = statement.order_by(Commitment.due_date)
    return list(session.execute(statement).all())


def list_approvals(
    session: Session, org_id: str, filters: PortfolioFilter
) -> list[tuple[Approval, Customer | None, Opportunity | None]]:
    statement = (
        select(Approval, Customer, Opportunity)
        .outerjoin(Customer, Approval.customer_id == Customer.id)
        .outerjoin(Opportunity, Approval.opportunity_id == Opportunity.id)
        .where(Approval.organisation_id == org_id)
    )
    if filters.owner_id:
        statement = statement.where(Customer.owner_id == filters.owner_id)
    if filters.region:
        statement = statement.where(Customer.region == filters.region)
    if filters.business_unit:
        statement = statement.where(Customer.business_unit == filters.business_unit)
    if filters.customer_id:
        statement = statement.where(Approval.customer_id == filters.customer_id)
    if filters.opportunity_id:
        statement = statement.where(Approval.opportunity_id == filters.opportunity_id)
    if filters.product:
        statement = statement.where(Opportunity.product == filters.product)
    if filters.pipeline:
        statement = statement.where(Opportunity.pipeline == filters.pipeline)
    if filters.stage:
        statement = statement.where(Opportunity.stage == filters.stage)
    statement = statement.order_by(Approval.created_at.desc())
    return list(session.execute(statement).all())


def list_interactions(
    session: Session,
    org_id: str,
    *,
    customer_id: str | None = None,
    opportunity_id: str | None = None,
) -> list[tuple[Interaction, SourceReference | None]]:
    statement = (
        select(Interaction, SourceReference)
        .outerjoin(SourceReference, Interaction.source_reference_id == SourceReference.id)
        .where(Interaction.organisation_id == org_id)
    )
    if customer_id:
        statement = statement.where(Interaction.customer_id == customer_id)
    if opportunity_id:
        statement = statement.where(Interaction.opportunity_id == opportunity_id)
    statement = statement.order_by(Interaction.occurred_at.desc())
    return list(session.execute(statement).all())


def list_intelligence(
    session: Session,
    org_id: str,
    *,
    customer_id: str | None = None,
    opportunity_id: str | None = None,
) -> list[IntelligenceRecord]:
    statement = select(IntelligenceRecord).where(IntelligenceRecord.organisation_id == org_id)
    if customer_id:
        statement = statement.where(IntelligenceRecord.customer_id == customer_id)
    if opportunity_id:
        statement = statement.where(IntelligenceRecord.opportunity_id == opportunity_id)
    statement = statement.order_by(IntelligenceRecord.created_at.desc())
    return list(session.scalars(statement))


def list_changes(session: Session, org_id: str) -> list[tuple[PortfolioChange, Customer | None]]:
    statement = (
        select(PortfolioChange, Customer)
        .outerjoin(Customer, PortfolioChange.customer_id == Customer.id)
        .where(PortfolioChange.organisation_id == org_id)
        .order_by(PortfolioChange.occurred_at.desc())
    )
    return list(session.execute(statement).all())


def list_sources(
    session: Session, org_id: str, *, customer_id: str | None = None
) -> list[SourceReference]:
    statement = select(SourceReference).where(SourceReference.organisation_id == org_id)
    if customer_id:
        statement = statement.where(SourceReference.customer_id == customer_id)
    return list(session.scalars(statement))


def list_files(session: Session, org_id: str) -> list[StoredFile]:
    statement = (
        select(StoredFile).where(StoredFile.organisation_id == org_id).order_by(StoredFile.uploaded_at.desc())
    )
    return list(session.scalars(statement))


def list_integrations(session: Session, org_id: str) -> list[Integration]:
    statement = select(Integration).where(Integration.organisation_id == org_id).order_by(Integration.name)
    return list(session.scalars(statement))


def get_integration(session: Session, org_id: str, key: str) -> Integration | None:
    statement = select(Integration).where(Integration.organisation_id == org_id, Integration.key == key)
    return session.scalars(statement).first()


def list_sync_jobs(session: Session, org_id: str) -> list[tuple[SyncJob, Integration]]:
    statement = (
        select(SyncJob, Integration)
        .join(Integration, SyncJob.integration_id == Integration.id)
        .where(SyncJob.organisation_id == org_id)
        .order_by(SyncJob.started_at.desc())
    )
    return list(session.execute(statement).all())


def list_audits(session: Session, org_id: str) -> list[AuditRecord]:
    statement = (
        select(AuditRecord)
        .where(AuditRecord.organisation_id == org_id)
        .order_by(AuditRecord.created_at.desc())
    )
    return list(session.scalars(statement))


def list_users(session: Session, org_id: str) -> list[User]:
    statement = select(User).where(User.organisation_id == org_id).order_by(User.name)
    return list(session.scalars(statement))


def get_setting(session: Session, org_id: str, key: str, default: str) -> str:
    statement = select(Setting).where(Setting.organisation_id == org_id, Setting.key == key)
    row = session.scalars(statement).first()
    if row is None:
        return default
    return row.value


def filter_options(session: Session, org_id: str) -> dict:
    users = list_users(session, org_id)
    customers = list(session.scalars(select(Customer).where(Customer.organisation_id == org_id).order_by(Customer.name)))
    deals = list(
        session.scalars(select(Opportunity).where(Opportunity.organisation_id == org_id).order_by(Opportunity.name))
    )
    pipelines = sorted({deal.pipeline for deal in deals})
    products = sorted({deal.product for deal in deals})
    stages = [
        "Qualification",
        "Discovery",
        "Proposal",
        "Negotiation",
        "Commit",
        "Closed Won",
        "Closed Lost",
    ]
    regions = sorted({customer.region for customer in customers})
    units = sorted({customer.business_unit for customer in customers})
    return {
        "owners": [{"id": user.id, "name": user.name} for user in users],
        "customers": [{"id": customer.id, "name": customer.name} for customer in customers],
        "opportunities": [{"id": deal.id, "name": deal.name, "customer_id": deal.customer_id} for deal in deals],
        "pipelines": pipelines,
        "products": products,
        "stages": stages,
        "regions": regions,
        "business_units": units,
    }


def integration_config(session: Session, integration_id: str) -> IntegrationConfig | None:
    statement = select(IntegrationConfig).where(IntegrationConfig.integration_id == integration_id)
    return session.scalars(statement).first()

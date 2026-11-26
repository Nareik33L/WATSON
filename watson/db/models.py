"""Core tables.

Phase 1 stores a simplified form of the entities later phases need: user,
organisation, customer (the company account), contact, opportunity, interaction,
commitment, approval, and source reference, plus the thin service tables
(integration, sync, audit, file, intelligence).

Every business row carries organisation_id so a later tenant split does not
require a new shape. data_origin is 'demo' or 'live'. This prototype writes
only demo.
"""

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from watson.db.base import Base


class Organisation(Base):
    __tablename__ = "organisations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    data_origin: Mapped[str] = mapped_column(String(16), default="demo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(64))
    region: Mapped[str] = mapped_column(String(64), default="")
    data_origin: Mapped[str] = mapped_column(String(16), default="demo")


class Customer(Base):
    """Company account. A separate company table can be split out later."""

    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    industry: Mapped[str] = mapped_column(String(120))
    region: Mapped[str] = mapped_column(String(64))
    business_unit: Mapped[str] = mapped_column(String(64))
    importance: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text, default="")
    data_origin: Mapped[str] = mapped_column(String(16), default="demo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_customer_region", "organisation_id", "region"),)


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(64), default="")
    active: Mapped[bool] = mapped_column(default=True)
    note: Mapped[str] = mapped_column(Text, default="")
    data_origin: Mapped[str] = mapped_column(String(16), default="demo")


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(240))
    product: Mapped[str] = mapped_column(String(120))
    pipeline: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(64))
    value: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="GBP")
    crm_probability: Mapped[int] = mapped_column(Integer)
    forecast_category: Mapped[str] = mapped_column(String(64))
    close_date: Mapped[date] = mapped_column(Date)
    previous_close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    previous_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    health: Mapped[str] = mapped_column(String(32))
    momentum: Mapped[str] = mapped_column(String(32))
    next_step: Mapped[str] = mapped_column(Text, default="")
    situation: Mapped[str] = mapped_column(Text, default="")
    last_interaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_interaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    simulated_probability: Mapped[int | None] = mapped_column(Integer, nullable=True)
    simulated_confidence: Mapped[str | None] = mapped_column(String(32), nullable=True)
    simulated_assumption: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_origin: Mapped[str] = mapped_column(String(16), default="demo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_opp_stage", "organisation_id", "stage"),)


class OpportunityContact(Base):
    __tablename__ = "opportunity_contacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), index=True)
    role: Mapped[str] = mapped_column(String(64))
    is_primary: Mapped[bool] = mapped_column(default=False)


class SourceReference(Base):
    __tablename__ = "source_references"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    opportunity_id: Mapped[str | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    source_system: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(200))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    data_origin: Mapped[str] = mapped_column(String(16), default="demo")


class Interaction(Base):
    __tablename__ = "interactions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    opportunity_id: Mapped[str | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True, index=True)
    source_reference_id: Mapped[str | None] = mapped_column(ForeignKey("source_references.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(String(240))
    summary: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    participants: Mapped[list] = mapped_column(JSON, default=list)
    data_origin: Mapped[str] = mapped_column(String(16), default="demo")


class Commitment(Base):
    __tablename__ = "commitments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    opportunity_id: Mapped[str | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True, index=True)
    source_reference_id: Mapped[str | None] = mapped_column(ForeignKey("source_references.id"), nullable=True)
    owner_name: Mapped[str] = mapped_column(String(200))
    owner_side: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text)
    importance: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    due_date: Mapped[date] = mapped_column(Date)
    evidence: Mapped[str] = mapped_column(Text, default="")
    related_recommendation: Mapped[str] = mapped_column(Text, default="")
    completion_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_origin: Mapped[str] = mapped_column(String(16), default="demo")


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    opportunity_id: Mapped[str | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True, index=True)
    source_reference_id: Mapped[str | None] = mapped_column(ForeignKey("source_references.id"), nullable=True)
    action_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(240))
    proposed_action: Mapped[str] = mapped_column(Text)
    source_system: Mapped[str] = mapped_column(String(32))
    current_value: Mapped[str] = mapped_column(Text, default="")
    proposed_value: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    impact: Mapped[str] = mapped_column(Text, default="")
    constraint_note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    decision_note: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_origin: Mapped[str] = mapped_column(String(16), default="demo")


class IntelligenceRecord(Base):
    __tablename__ = "intelligence_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    opportunity_id: Mapped[str | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True, index=True)
    source_reference_id: Mapped[str | None] = mapped_column(ForeignKey("source_references.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    producer: Mapped[str] = mapped_column(String(32), default="demo_record")
    data_origin: Mapped[str] = mapped_column(String(16), default="demo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PortfolioChange(Base):
    __tablename__ = "portfolio_changes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    opportunity_id: Mapped[str | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    source_system: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_origin: Mapped[str] = mapped_column(String(16), default="demo")


class Integration(Base):
    __tablename__ = "integrations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    key: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(120))
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="simulated")
    mode: Mapped[str] = mapped_column(String(32), default="simulated")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    records_seen: Mapped[int] = mapped_column(Integer, default=0)
    data_origin: Mapped[str] = mapped_column(String(16), default="demo")

    __table_args__ = (UniqueConstraint("organisation_id", "key", name="uq_integration_key"),)


class IntegrationConfig(Base):
    __tablename__ = "integration_configs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    integration_id: Mapped[str] = mapped_column(ForeignKey("integrations.id"), index=True)
    poll_interval_minutes: Mapped[int] = mapped_column(Integer, default=15)
    enabled: Mapped[bool] = mapped_column(default=False)
    notes: Mapped[str] = mapped_column(Text, default="")


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    integration_id: Mapped[str] = mapped_column(ForeignKey("integrations.id"), index=True)
    status: Mapped[str] = mapped_column(String(32))
    trigger: Mapped[str] = mapped_column(String(32))
    records_processed: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SyncEvent(Base):
    __tablename__ = "sync_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    integration_id: Mapped[str] = mapped_column(ForeignKey("integrations.id"), index=True)
    sync_job_id: Mapped[str] = mapped_column(ForeignKey("sync_jobs.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text, default="")
    dedup_key: Mapped[str] = mapped_column(String(160), default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditRecord(Base):
    __tablename__ = "audit_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class StoredFile(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(240))
    kind: Mapped[str] = mapped_column(String(32))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    issue_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")
    findings: Mapped[list] = mapped_column(JSON, default=list)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_origin: Mapped[str] = mapped_column(String(16), default="demo")


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    key: Mapped[str] = mapped_column(String(120))
    value: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (UniqueConstraint("organisation_id", "key", name="uq_setting_org_key"),)

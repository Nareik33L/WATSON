"""Assemble API payloads from the database and the rules service."""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from watson.constants import ACTION_LABELS, CLOSED_STAGES, COMMITMENT_STATUSES, HEALTH_RANK, SOURCE_LABELS, TERMINAL_COMMITMENT_STATUSES
from watson.dal.filters import PortfolioFilter
from watson.dal import queries
from watson.db.models import Approval, Commitment, Customer, Integration, Opportunity, SyncJob, User
from watson.services import intelligence
from watson.services.integration import poll_interval
from watson.status import effective_status
from watson.tenant import DEMO_USER_ID

STAGE_ORDER = [
    "Qualification",
    "Discovery",
    "Proposal",
    "Negotiation",
    "Commit",
    "Closed Won",
    "Closed Lost",
]
CATEGORY_ORDER = ["Commit", "Best case", "Pipeline", "Closed", "Omitted"]


def health(session: Session) -> dict:
    session.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "phase": 1,
        "demo": True,
        "database": "sqlite",
        "external_ai": "disabled",
        "live_integrations": "disabled",
    }


def meta(session: Session, org_id: str) -> dict:
    from watson.db.models import Organisation

    org = session.get(Organisation, org_id)
    user = session.get(User, DEMO_USER_ID)
    approvals = queries.list_approvals(session, org_id, PortfolioFilter())
    commitments = _commitment_items(session, org_id, PortfolioFilter())
    today = date.today()
    return {
        "product": "Sales Command Centre",
        "phase": 1,
        "demo": True,
        "data_origin": org.data_origin if org else "demo",
        "today": today.isoformat(),
        "date_label": f"{today.strftime('%A')} {today.day} {today.strftime('%B %Y')}",
        "disclaimer": (
            "Fictional demo data. Outlook, Gong, HubSpot, Excel, and Calendly are not connected. "
            "No AI model is running."
        ),
        "user": {
            "id": user.id if user else DEMO_USER_ID,
            "name": user.name if user else "Alex Harrington",
            "email": user.email if user else "",
            "role": user.role if user else "",
        },
        "organisation": {"id": org_id, "name": org.name if org else "Helios Industrial"},
        "counts": {
            "pending_approvals": sum(1 for item in approvals if item[0].status == "pending"),
            "overdue_commitments": sum(1 for item in commitments if item["status"] == "Overdue"),
        },
    }


def filters(session: Session, org_id: str) -> dict:
    return queries.filter_options(session, org_id)


def overview(session: Session, org_id: str, filters: PortfolioFilter) -> dict:
    deals = _open_deals(session, org_id, filters)
    all_deals = _deal_items(session, org_id, filters)
    commitments = _commitment_items(session, org_id, filters)
    meetings = _meetings(session, org_id, filters, commitments)
    priorities = intelligence.rank_priorities(deals, commitments, meetings)
    today_meetings = [meeting for meeting in meetings if meeting["day"] in {"today", "upcoming"}]
    pipeline = sum(deal["value"] for deal in deals)
    crm_weighted = sum(_weighted(deal["value"], deal["crm_probability"]) for deal in deals)
    simulated = sum(_weighted(deal["value"], deal["simulated_probability"]) for deal in deals)
    at_risk = sum(1 for deal in deals if deal["health"] in {"at_risk", "stalled"})
    overdue = sum(1 for item in commitments if item["status"] == "Overdue")
    pending = [
        approval_dict(*row)
        for row in queries.list_approvals(session, org_id, filters)
        if row[0].status == "pending"
    ]
    attention = [deal for deal in deals if _needs_attention(deal)]
    attention.sort(key=lambda deal: (HEALTH_RANK.get(deal["health"], 9), -deal["value"]))
    moving = [
        deal
        for deal in deals
        if deal["health"] == "healthy" and deal["momentum"] == "improving"
    ]
    soon = [
        item
        for item in commitments
        if item["status"] in {"Overdue", "Due today", "New"}
        or (
            item["status"] == "Upcoming"
            and date.fromisoformat(item["due_date"]) <= date.today() + timedelta(days=7)
        )
    ]
    focus = priorities[0]["title"] if priorities else "Nothing in this view needs you today."
    return {
        "demo": True,
        "greeting": _greeting(),
        "date_label": _date_label(),
        "today": date.today().isoformat(),
        "focus": focus,
        "priorities": priorities[:3],
        "method_note": intelligence.METHOD_NOTE,
        "totals": {
            "open_count": len(deals),
            "pipeline": pipeline,
            "crm_weighted": crm_weighted,
            "simulated_weighted": simulated,
            "at_risk": at_risk,
            "overdue_commitments": overdue,
            "pending_approvals": len(pending),
            "meetings": len(today_meetings),
        },
        "attention": attention[:8],
        "moving": moving[:4],
        "commitments": soon[:6],
        "activity": _activity_items(session, org_id, filters, limit=8),
        "approvals": pending[:4],
        "filters_active": filters.any(),
        "closed_in_view": sum(1 for deal in all_deals if deal["stage"] in CLOSED_STAGES),
    }


def briefing(session: Session, org_id: str, filters: PortfolioFilter) -> dict:
    deals = _deal_items(session, org_id, filters)
    open_deals = [deal for deal in deals if deal["stage"] not in CLOSED_STAGES]
    commitments = _commitment_items(session, org_id, filters)
    meetings = _meetings(session, org_id, filters, commitments)
    changes = _changes(session, org_id, filters, deals, commitments)
    follow_ups = [
        item
        for item in commitments
        if item["status"] in {"Overdue", "Due today", "Unclear", "New"}
        or (
            item["status"] == "Upcoming"
            and date.fromisoformat(item["due_date"]) <= date.today() + timedelta(days=14)
        )
    ]
    return {
        "demo": True,
        "method_note": intelligence.METHOD_NOTE,
        "filters_active": filters.any(),
        "priorities": intelligence.rank_priorities(open_deals, commitments, meetings),
        "meetings": [meeting for meeting in meetings if meeting["day"] == "today"],
        "follow_ups": follow_ups,
        "changes": changes[:12],
        "alerts": intelligence.deal_alerts(open_deals, commitments)[:8],
    }


def deals(session: Session, org_id: str, filters: PortfolioFilter, scope: str) -> dict:
    items = _deal_items(session, org_id, filters)
    if scope != "all" and filters.stage not in CLOSED_STAGES:
        items = [item for item in items if item["stage"] not in CLOSED_STAGES]
    return {"demo": True, "items": items, "count": len(items)}


def deal_detail(session: Session, org_id: str, deal_id: str) -> dict | None:
    row = queries.get_deal(session, org_id, deal_id)
    if row is None:
        return None
    deal = deal_dict(*row)
    commitments = [
        item
        for item in _commitment_items(session, org_id, PortfolioFilter())
        if item["opportunity_id"] == deal_id
    ]
    interactions = queries.list_interactions(session, org_id, opportunity_id=deal_id)
    stakeholders = [
        {
            "id": contact.id,
            "name": contact.name,
            "title": contact.title,
            "email": contact.email,
            "phone": contact.phone,
            "role": link.role,
            "is_primary": link.is_primary,
            "active": contact.active,
            "note": contact.note,
        }
        for link, contact in queries.list_stakeholders(session, org_id, deal_id)
    ]
    return {
        "demo": True,
        "deal": deal,
        "stakeholders": stakeholders,
        "commitments": commitments,
        "timeline": _timeline_from_interactions(interactions),
        "intelligence": _intelligence(session, org_id, opportunity_id=deal_id),
        "simulated_forecast": intelligence.simulated_forecast(deal),
    }


def customers(session: Session, org_id: str, filters: PortfolioFilter) -> dict:
    accounts = queries.list_customers(session, org_id, filters)
    deal_items = _deal_items(session, org_id, filters)
    if filters.restricts_deals():
        allowed = {deal["customer_id"] for deal in deal_items}
        accounts = [account for account in accounts if account.id in allowed]
    by_customer: dict[str, list[dict]] = defaultdict(list)
    for deal in deal_items:
        by_customer[deal["customer_id"]].append(deal)
    owners = {user.id: user.name for user in queries.list_users(session, org_id)}
    items = []
    for account in accounts:
        owned = by_customer.get(account.id, [])
        open_deals = [deal for deal in owned if deal["stage"] not in CLOSED_STAGES]
        worst = None
        if open_deals:
            worst = sorted(open_deals, key=lambda deal: HEALTH_RANK.get(deal["health"], 9))[0]["health"]
        last = [deal["last_interaction_at"] for deal in owned if deal["last_interaction_at"]]
        items.append(
            {
                "id": account.id,
                "name": account.name,
                "industry": account.industry,
                "region": account.region,
                "business_unit": account.business_unit,
                "importance": account.importance,
                "summary": account.summary,
                "owner_id": account.owner_id,
                "owner_name": owners.get(account.owner_id, ""),
                "open_count": len(open_deals),
                "open_pipeline": sum(deal["value"] for deal in open_deals),
                "health": worst,
                "last_interaction_at": max(last) if last else None,
                "data_origin": account.data_origin,
            }
        )
    return {"demo": True, "items": items, "count": len(items)}


def customer_detail(session: Session, org_id: str, customer_id: str) -> dict | None:
    account = queries.get_customer(session, org_id, customer_id)
    if account is None:
        return None
    owners = {user.id: user.name for user in queries.list_users(session, org_id)}
    deal_items = [
        deal_dict(*row)
        for row in queries.list_deals(session, org_id, PortfolioFilter(customer_id=customer_id))
    ]
    commitments = [
        item
        for item in _commitment_items(session, org_id, PortfolioFilter())
        if item["customer_id"] == customer_id
    ]
    interactions = queries.list_interactions(session, org_id, customer_id=customer_id)
    timeline = _timeline_from_interactions(interactions)
    timeline.extend(_commitment_events(commitments))
    changes = queries.list_changes(session, org_id)
    for change, _customer in changes:
        if change.customer_id != customer_id:
            continue
        timeline.append(
            {
                "id": change.id,
                "occurred_at": iso_dt(change.occurred_at),
                "kind": "change",
                "source_system": change.source_system,
                "source_label": SOURCE_LABELS.get(change.source_system, change.source_system),
                "source_reference": "",
                "title": change.kind.replace("_", " ").title(),
                "summary": change.summary,
                "direction": None,
            }
        )
    timeline.sort(key=lambda item: item["occurred_at"] or "", reverse=True)
    return {
        "demo": True,
        "customer": {
            "id": account.id,
            "name": account.name,
            "industry": account.industry,
            "region": account.region,
            "business_unit": account.business_unit,
            "importance": account.importance,
            "summary": account.summary,
            "owner_id": account.owner_id,
            "owner_name": owners.get(account.owner_id, ""),
            "data_origin": account.data_origin,
        },
        "contacts": [
            {
                "id": contact.id,
                "name": contact.name,
                "title": contact.title,
                "email": contact.email,
                "phone": contact.phone,
                "active": contact.active,
                "note": contact.note,
            }
            for contact in queries.list_contacts(session, org_id, customer_id)
        ],
        "opportunities": deal_items,
        "commitments": commitments,
        "timeline": timeline,
        "intelligence": _intelligence(session, org_id, customer_id=customer_id),
    }


def commitments(session: Session, org_id: str, filters: PortfolioFilter, status: str | None) -> dict:
    items = _commitment_items(session, org_id, filters)
    counts = Counter(item["status"] for item in items)
    if status:
        items = [item for item in items if item["status"] == status]
    order = {name: index for index, name in enumerate(COMMITMENT_STATUSES)}
    items.sort(key=lambda item: (order.get(item["status"], 99), item["due_date"]))
    return {
        "demo": True,
        "items": items,
        "counts": {name: counts.get(name, 0) for name in COMMITMENT_STATUSES},
    }


def approvals(session: Session, org_id: str, filters: PortfolioFilter, status: str | None) -> dict:
    items = [approval_dict(*row) for row in queries.list_approvals(session, org_id, filters)]
    counts = {
        "pending": sum(1 for item in items if item["status"] == "pending"),
        "deferred": sum(1 for item in items if item["status"] == "deferred"),
        "decided": sum(1 for item in items if item["status"] in {"approved", "rejected"}),
    }
    if status == "pending":
        items = [item for item in items if item["status"] == "pending"]
    elif status == "deferred":
        items = [item for item in items if item["status"] == "deferred"]
    elif status == "decided":
        items = [item for item in items if item["status"] in {"approved", "rejected"}]
    return {"demo": True, "items": items, "counts": counts}


def forecast(session: Session, org_id: str, filters: PortfolioFilter) -> dict:
    deals = _deal_items(session, org_id, filters)
    open_deals = [deal for deal in deals if deal["stage"] not in CLOSED_STAGES]
    won = [deal for deal in deals if deal["stage"] == "Closed Won"]
    lost = [deal for deal in deals if deal["stage"] == "Closed Lost"]
    crm_weighted = sum(_weighted(deal["value"], deal["crm_probability"]) for deal in open_deals)
    simulated = sum(_weighted(deal["value"], deal["simulated_probability"]) for deal in open_deals)
    pipeline = sum(deal["value"] for deal in open_deals)
    commit_only = sum(
        _weighted(deal["value"], deal["crm_probability"])
        for deal in open_deals
        if deal["forecast_category"] == "Commit"
    )
    slips = sum(
        _weighted(deal["value"], deal["crm_probability"])
        for deal in open_deals
        if deal["momentum"] == "deteriorating" or deal["health"] == "stalled"
    )
    by_stage_map: dict[str, dict] = {}
    by_month_map: dict[str, dict] = {}
    by_category_map: dict[str, dict] = {}
    for deal in open_deals:
        stage = by_stage_map.setdefault(
            deal["stage"], {"stage": deal["stage"], "value": 0, "crm_weighted": 0, "simulated": 0, "count": 0}
        )
        month = by_month_map.setdefault(
            deal["close_date"][:7],
            {"month": deal["close_date"][:7], "value": 0, "crm_weighted": 0, "simulated": 0, "count": 0},
        )
        category = by_category_map.setdefault(
            deal["forecast_category"],
            {"category": deal["forecast_category"], "value": 0, "crm_weighted": 0, "count": 0},
        )
        for bucket, include_simulated in ((stage, True), (month, True)):
            bucket["value"] += deal["value"]
            bucket["crm_weighted"] += _weighted(deal["value"], deal["crm_probability"])
            if include_simulated:
                bucket["simulated"] += _weighted(deal["value"], deal["simulated_probability"])
            bucket["count"] += 1
        category["value"] += deal["value"]
        category["crm_weighted"] += _weighted(deal["value"], deal["crm_probability"])
        category["count"] += 1
    discrepancies = []
    for deal in open_deals:
        simulated_probability = deal["simulated_probability"]
        gap = None if simulated_probability is None else deal["crm_probability"] - simulated_probability
        slipped = bool(deal["previous_close_date"] and deal["previous_close_date"] != deal["close_date"])
        if (gap is not None and abs(gap) >= 15) or slipped:
            notes = []
            if gap is not None and abs(gap) >= 15:
                side = "above" if gap > 0 else "below"
                notes.append(
                    f"CRM probability is {abs(gap)} points {side} the simulated placeholder."
                )
            if slipped:
                notes.append(
                    f"Close date moved from {deal['previous_close_date']} to {deal['close_date']}."
                )
            discrepancies.append(
                {
                    "id": deal["id"],
                    "name": deal["name"],
                    "customer_name": deal["customer_name"],
                    "crm_probability": deal["crm_probability"],
                    "simulated_probability": simulated_probability,
                    "gap": gap,
                    "note": " ".join(notes),
                    "data_origin": "demo",
                }
            )
    discrepancies.sort(key=lambda item: abs(item["gap"] or 0), reverse=True)
    markdowns = [
        {
            "customer_name": deal["customer_name"],
            "opportunity_id": deal["id"],
            "name": deal["name"],
            "gap": deal["crm_probability"] - deal["simulated_probability"],
        }
        for deal in open_deals
        if deal["simulated_probability"] is not None and deal["crm_probability"] - deal["simulated_probability"] >= 15
    ]
    markdowns.sort(key=lambda item: item["gap"] * 1, reverse=True)
    return {
        "demo": True,
        "label": "Demo data",
        "disclaimer": intelligence.SIMULATED_DISCLAIMER,
        "totals": {
            "pipeline": pipeline,
            "crm_weighted": crm_weighted,
            "simulated_weighted": simulated,
            "conservative": commit_only,
            "optimistic": pipeline,
            "likely_slips": slips,
            "gap": crm_weighted - simulated,
        },
        "won": {"count": len(won), "value": sum(deal["value"] for deal in won)},
        "lost": {"count": len(lost), "value": sum(deal["value"] for deal in lost)},
        "by_stage": [by_stage_map[name] for name in STAGE_ORDER if name in by_stage_map],
        "by_month": [by_month_map[key] for key in sorted(by_month_map)],
        "by_category": [by_category_map[name] for name in CATEGORY_ORDER if name in by_category_map],
        "discrepancies": discrepancies,
        "largest_markdowns": markdowns[:3],
        "risks": intelligence.deal_alerts(open_deals, _commitment_items(session, org_id, filters))[:6],
    }


def files(session: Session, org_id: str) -> dict:
    return {
        "demo": True,
        "note": "Workbook analysis is not active in this prototype. The rows below are fictional.",
        "items": [
            {
                "id": stored.id,
                "name": stored.name,
                "kind": stored.kind,
                "row_count": stored.row_count,
                "issue_count": stored.issue_count,
                "summary": stored.summary,
                "findings": stored.findings,
                "uploaded_at": iso_dt(stored.uploaded_at),
                "data_origin": stored.data_origin,
            }
            for stored in queries.list_files(session, org_id)
        ],
    }


def activity(session: Session, org_id: str, kind: str | None, source: str | None) -> dict:
    selected = kind or "all"
    items = _feed(session, org_id, selected)
    if source:
        items = [item for item in items if item.get("source_system") == source]
    items.sort(key=lambda item: item["occurred_at"] or "", reverse=True)
    return {"demo": True, "items": items[:150]}


def integrations(session: Session, org_id: str) -> dict:
    minutes = poll_interval(session, org_id)
    items = []
    for integration in queries.list_integrations(session, org_id):
        items.append(_integration_dict(integration, minutes))
    jobs = [_job_dict(job, integration) for job, integration in queries.list_sync_jobs(session, org_id)]
    return {
        "demo": True,
        "disclaimer": (
            "These cards are simulated. Refresh writes a local log line and does not contact "
            "Outlook, Gong, HubSpot, Excel, or Calendly."
        ),
        "poll_interval_minutes": minutes,
        "polling": False,
        "items": items,
        "jobs": jobs[:20],
    }


def integration_payload(integration: Integration, session: Session, org_id: str) -> dict:
    return _integration_dict(integration, poll_interval(session, org_id))


def job_payload(job: SyncJob, integration: Integration) -> dict:
    return _job_dict(job, integration)


def settings_payload(session: Session, org_id: str, database_name: str) -> dict:
    info = meta(session, org_id)
    return {
        "phase": 1,
        "demo": True,
        "user": info["user"],
        "organisation": info["organisation"],
        "database": "sqlite",
        "database_file": database_name,
        "encryption": "Not enabled in this phase. The file holds fictional data only.",
        "external_ai": "disabled",
        "live_integrations": "disabled",
        "poll_interval_minutes": poll_interval(session, org_id),
        "poll_note": "Stored for a later phase. Nothing is polled.",
        "privacy": [
            "The server binds to localhost only.",
            "No customer data is sent to an external AI service.",
            "Approvals are saved locally and do not call Outlook, Gong, HubSpot, Excel, or Calendly.",
            "Full email bodies, transcripts, recordings, and raw workbooks are not stored.",
            "This prototype does not ask you to sign in. Actions are recorded as Alex Harrington, the demo user.",
        ],
    }


def commitment_dict(commitment: Commitment, customer: Customer, opportunity: Opportunity | None) -> dict:
    return {
        "id": commitment.id,
        "customer_id": customer.id,
        "customer_name": customer.name,
        "opportunity_id": opportunity.id if opportunity else None,
        "opportunity_name": opportunity.name if opportunity else None,
        "owner_name": commitment.owner_name,
        "owner_side": commitment.owner_side,
        "description": commitment.description,
        "importance": commitment.importance,
        "status": effective_status(commitment.status, commitment.due_date),
        "stored_status": commitment.status,
        "due_date": commitment.due_date.isoformat(),
        "evidence": commitment.evidence,
        "related_recommendation": commitment.related_recommendation,
        "completion_note": commitment.completion_note,
        "created_at": iso_dt(commitment.created_at),
        "completed_at": iso_dt(commitment.completed_at),
        "data_origin": commitment.data_origin,
    }


def approval_dict(approval: Approval, customer: Customer | None, opportunity: Opportunity | None) -> dict:
    return {
        "id": approval.id,
        "customer_id": customer.id if customer else None,
        "customer_name": customer.name if customer else None,
        "opportunity_id": opportunity.id if opportunity else None,
        "opportunity_name": opportunity.name if opportunity else None,
        "action_type": approval.action_type,
        "action_label": ACTION_LABELS.get(approval.action_type, approval.action_type),
        "title": approval.title,
        "proposed_action": approval.proposed_action,
        "source_system": approval.source_system,
        "source_label": SOURCE_LABELS.get(approval.source_system, approval.source_system),
        "current_value": approval.current_value,
        "proposed_value": approval.proposed_value,
        "reason": approval.reason,
        "evidence": approval.evidence,
        "impact": approval.impact,
        "constraint_note": approval.constraint_note,
        "status": approval.status,
        "revision": approval.revision,
        "decision_note": approval.decision_note,
        "decided_by": approval.decided_by,
        "created_at": iso_dt(approval.created_at),
        "decided_at": iso_dt(approval.decided_at),
        "data_origin": approval.data_origin,
        "result_note": "Saved locally. No external system was contacted."
        if approval.status != "pending"
        else "",
    }


def deal_dict(opportunity: Opportunity, customer: Customer, owner: User) -> dict:
    return {
        "id": opportunity.id,
        "name": opportunity.name,
        "customer_id": customer.id,
        "customer_name": customer.name,
        "stage": opportunity.stage,
        "product": opportunity.product,
        "pipeline": opportunity.pipeline,
        "value": opportunity.value,
        "currency": opportunity.currency,
        "close_date": opportunity.close_date.isoformat(),
        "previous_close_date": opportunity.previous_close_date.isoformat() if opportunity.previous_close_date else None,
        "previous_stage": opportunity.previous_stage,
        "health": opportunity.health,
        "momentum": opportunity.momentum,
        "crm_probability": opportunity.crm_probability,
        "simulated_probability": opportunity.simulated_probability,
        "simulated_confidence": opportunity.simulated_confidence,
        "simulated_assumption": opportunity.simulated_assumption,
        "forecast_category": opportunity.forecast_category,
        "owner_id": owner.id,
        "owner_name": owner.name,
        "next_step": opportunity.next_step,
        "situation": opportunity.situation,
        "last_interaction_at": iso_dt(opportunity.last_interaction_at),
        "next_interaction_at": iso_dt(opportunity.next_interaction_at),
        "region": customer.region,
        "business_unit": customer.business_unit,
        "industry": customer.industry,
        "importance": customer.importance,
        "data_origin": opportunity.data_origin,
    }


def _deal_items(session: Session, org_id: str, filters: PortfolioFilter) -> list[dict]:
    return [deal_dict(*row) for row in queries.list_deals(session, org_id, filters)]


def _open_deals(session: Session, org_id: str, filters: PortfolioFilter) -> list[dict]:
    return [deal for deal in _deal_items(session, org_id, filters) if deal["stage"] not in CLOSED_STAGES]


def _commitment_items(session: Session, org_id: str, filters: PortfolioFilter) -> list[dict]:
    return [commitment_dict(*row) for row in queries.list_commitments(session, org_id, filters)]


def _meetings(session: Session, org_id: str, filters: PortfolioFilter, commitments: list[dict]) -> list[dict]:
    rows = queries.list_interactions(session, org_id)
    allowed = _allowed_customers(filters, _deal_items(session, org_id, filters), commitments)
    today = date.today()
    meetings = []
    for interaction, source in rows:
        if interaction.type not in {"meeting", "booking"}:
            continue
        if allowed is not None and interaction.customer_id not in allowed:
            continue
        when = as_utc(interaction.occurred_at)
        marker = _day_marker(when.date(), today)
        if marker is None:
            continue
        related = [
            item["description"]
            for item in commitments
            if item["customer_id"] == interaction.customer_id and item["status"] not in TERMINAL_COMMITMENT_STATUSES
        ]
        meetings.append(
            {
                "id": interaction.id,
                "occurred_at": iso_dt(when),
                "time_label": when.strftime("%H:%M"),
                "day": marker,
                "title": interaction.title,
                "summary": interaction.summary,
                "prep": interaction.detail,
                "customer_id": interaction.customer_id,
                "customer_name": _customer_name(session, interaction.customer_id),
                "opportunity_id": interaction.opportunity_id,
                "participants": interaction.participants or [],
                "source_system": source.source_system if source else "manual",
                "source_label": SOURCE_LABELS.get(source.source_system, "Manual") if source else "Manual",
                "commitments": related[:4],
            }
        )
    meetings.sort(key=lambda item: item["occurred_at"] or "")
    return meetings


def _changes(session, org_id, filters, deals, commitments) -> list[dict]:
    allowed = _allowed_customers(filters, deals, commitments)
    items = []
    for change, customer in queries.list_changes(session, org_id):
        if allowed is not None and change.customer_id not in allowed:
            continue
        items.append(
            {
                "id": change.id,
                "occurred_at": iso_dt(change.occurred_at),
                "kind": change.kind,
                "summary": change.summary,
                "customer_id": change.customer_id,
                "customer_name": customer.name if customer else "",
                "opportunity_id": change.opportunity_id,
                "source_system": change.source_system,
                "source_label": SOURCE_LABELS.get(change.source_system, change.source_system),
            }
        )
    return items


def _activity_items(session, org_id, filters, limit: int) -> list[dict]:
    items = _feed(session, org_id, "interaction")
    allowed = None
    if filters.any():
        allowed = _allowed_customers(
            filters,
            _deal_items(session, org_id, filters),
            _commitment_items(session, org_id, filters),
        )
    if allowed is not None:
        items = [item for item in items if item.get("customer_id") in allowed]
    return items[:limit]


def _feed(session: Session, org_id: str, kind: str) -> list[dict]:
    items: list[dict] = []
    if kind in {"all", "interaction"}:
        for interaction, source in queries.list_interactions(session, org_id):
            system = source.source_system if source else "manual"
            items.append(
                {
                    "id": interaction.id,
                    "occurred_at": iso_dt(interaction.occurred_at),
                    "kind": "interaction",
                    "source_system": system,
                    "source_label": SOURCE_LABELS.get(system, system),
                    "title": interaction.title,
                    "summary": interaction.summary,
                    "customer_id": interaction.customer_id,
                    "href": f"/deals/{interaction.opportunity_id}"
                    if interaction.opportunity_id
                    else f"/customers/{interaction.customer_id}",
                }
            )
    if kind in {"all", "audit", "approval"}:
        for entry in queries.list_audits(session, org_id):
            if kind == "approval" and entry.entity_type != "approval":
                continue
            if kind == "audit" and entry.entity_type in {"approval", "integration"}:
                continue
            if kind == "all" and entry.entity_type == "integration":
                continue
            bucket = "approval" if entry.entity_type == "approval" else "audit"
            items.append(
                {
                    "id": entry.id,
                    "occurred_at": iso_dt(entry.created_at),
                    "kind": bucket,
                    "source_system": None,
                    "source_label": "Audit",
                    "title": f"{entry.actor} · {entry.action}",
                    "summary": entry.detail,
                    "customer_id": None,
                    "href": "/approvals" if entry.entity_type == "approval" else "/activity",
                }
            )
    if kind in {"all", "sync"}:
        for job, integration in queries.list_sync_jobs(session, org_id):
            items.append(
                {
                    "id": job.id,
                    "occurred_at": iso_dt(job.started_at),
                    "kind": "sync",
                    "source_system": integration.key,
                    "source_label": integration.name,
                    "title": f"{integration.name} · {job.trigger}",
                    "summary": job.message,
                    "customer_id": None,
                    "href": "/integrations",
                }
            )
    return items


def _timeline_from_interactions(rows) -> list[dict]:
    items = []
    for interaction, source in rows:
        system = source.source_system if source else "manual"
        items.append(
            {
                "id": interaction.id,
                "occurred_at": iso_dt(interaction.occurred_at),
                "kind": interaction.type,
                "source_system": system,
                "source_label": SOURCE_LABELS.get(system, system),
                "source_reference": source.external_id if source else "",
                "title": interaction.title,
                "summary": interaction.summary,
                "direction": interaction.direction,
            }
        )
    items.sort(key=lambda item: item["occurred_at"] or "", reverse=True)
    return items


def _commitment_events(commitments: list[dict]) -> list[dict]:
    items = []
    for item in commitments:
        items.append(
            {
                "id": f"{item['id']}-open",
                "occurred_at": item["created_at"],
                "kind": "commitment",
                "source_system": "manual",
                "source_label": "Commitment",
                "source_reference": "",
                "title": item["description"],
                "summary": f"{item['status']} · {item['owner_name']} · due {item['due_date']}. {item['evidence']}",
                "direction": None,
            }
        )
    return items


def _intelligence(session, org_id, *, customer_id=None, opportunity_id=None) -> list[dict]:
    records = queries.list_intelligence(
        session, org_id, customer_id=customer_id, opportunity_id=opportunity_id
    )
    return [
        {
            "id": record.id,
            "category": record.category,
            "summary": record.summary,
            "producer": record.producer,
            "simulated": record.producer == "simulated_placeholder",
            "created_at": iso_dt(record.created_at),
            "data_origin": record.data_origin,
        }
        for record in records
    ]


def _integration_dict(integration: Integration, minutes: int) -> dict:
    last = as_utc(integration.last_sync_at) if integration.last_sync_at else None
    nxt = last + timedelta(minutes=minutes) if last else None
    return {
        "id": integration.id,
        "key": integration.key,
        "name": integration.name,
        "summary": integration.summary,
        "status": integration.status,
        "mode": integration.mode,
        "last_sync_at": iso_dt(last),
        "next_sync_at": iso_dt(nxt),
        "records_seen": integration.records_seen,
        "connected": False,
        "data_origin": integration.data_origin,
    }


def _job_dict(job: SyncJob, integration: Integration) -> dict:
    return {
        "id": job.id,
        "integration": integration.name,
        "key": integration.key,
        "status": job.status,
        "trigger": job.trigger,
        "records_processed": job.records_processed,
        "message": job.message,
        "started_at": iso_dt(job.started_at),
        "finished_at": iso_dt(job.finished_at),
    }


def _allowed_customers(filters: PortfolioFilter, deals: list[dict], commitments: list[dict]) -> set[str] | None:
    if not filters.any():
        return None
    return {deal["customer_id"] for deal in deals} | {item["customer_id"] for item in commitments}


def _needs_attention(deal: dict) -> bool:
    return deal["health"] in {"at_risk", "stalled", "watch"} or deal["momentum"] == "deteriorating"


def _customer_name(session: Session, customer_id: str) -> str:
    customer = session.get(Customer, customer_id)
    return customer.name if customer else ""


def _date_label() -> str:
    today = date.today()
    return f"{today.strftime('%A')} {today.day} {today.strftime('%B %Y')}"


def _greeting() -> str:
    hour = datetime.now(timezone.utc).hour
    if hour < 12:
        part = "Good morning"
    elif hour < 17:
        part = "Good afternoon"
    else:
        part = "Good evening"
    return f"{part}, Alex"


def _day_marker(day: date, today: date) -> str | None:
    if day == today:
        return "today"
    if today < day <= today + timedelta(days=7):
        return "upcoming"
    return None


def _weighted(value: int, probability: int | None) -> int:
    if probability is None:
        return 0
    return int(round(value * probability / 100))


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso_dt(value: datetime | None) -> str | None:
    when = as_utc(value)
    if when is None:
        return None
    return when.isoformat()

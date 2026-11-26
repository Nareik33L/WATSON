"""Shared labels. Phase 1 keeps these in one place so later phases can extend them."""

CLOSED_STAGES = frozenset({"Closed Won", "Closed Lost"})

COMMITMENT_STATUSES = (
    "New",
    "Upcoming",
    "Due today",
    "Overdue",
    "Completed",
    "Cancelled",
    "Unclear",
)

TERMINAL_COMMITMENT_STATUSES = frozenset({"Completed", "Cancelled", "Unclear"})

SOURCE_LABELS = {
    "outlook": "Outlook",
    "gong": "Gong",
    "hubspot": "HubSpot",
    "excel": "Excel",
    "calendly": "Calendly",
    "manual": "Manual",
}

ACTION_LABELS = {
    "hubspot_field": "HubSpot field update",
    "crm_note": "CRM note",
    "contact_update": "Contact update",
    "outlook_draft": "Outlook draft",
    "excel_version": "Excel new version",
    "commitment_update": "Commitment update",
    "association": "Record association",
}

HEALTH_RANK = {"stalled": 0, "at_risk": 1, "watch": 2, "healthy": 3}

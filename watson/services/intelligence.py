"""Rules over demo records.

This is not a model. Phase 2 introduces a local model behind a separate
service. The functions here only rank and label data already in the database.
"""

SIMULATED_DISCLAIMER = "No model was called. This figure is fictional demo data."
METHOD_NOTE = (
    "Ranked from the demo records using due dates, meeting times, deal value, and health. "
    "No model was used."
)


def rank_priorities(deals: list[dict], commitments: list[dict], meetings: list[dict]) -> list[dict]:
    items: list[dict] = []
    for commitment in commitments:
        if commitment["status"] == "Overdue" and commitment["importance"] == "high":
            items.append(
                {
                    "id": commitment["id"],
                    "score": 100,
                    "urgency": "high",
                    "kind": "commitment",
                    "title": f"Overdue: {commitment['description']}",
                    "context": commitment["customer_name"],
                    "customer_id": commitment["customer_id"],
                    "opportunity_id": commitment["opportunity_id"],
                }
            )
        elif commitment["status"] == "Due today" and commitment["importance"] == "high":
            items.append(
                {
                    "id": commitment["id"],
                    "score": 80,
                    "urgency": "high",
                    "kind": "commitment",
                    "title": f"Due today: {commitment['description']}",
                    "context": commitment["customer_name"],
                    "customer_id": commitment["customer_id"],
                    "opportunity_id": commitment["opportunity_id"],
                }
            )
    for meeting in meetings:
        if meeting.get("day") != "today":
            continue
        items.append(
            {
                "id": meeting["id"],
                "score": 90,
                "urgency": "high",
                "kind": "meeting",
                "title": f"Prepare for {meeting['customer_name']} at {meeting['time_label']}",
                "context": meeting["title"],
                "customer_id": meeting["customer_id"],
                "opportunity_id": meeting["opportunity_id"],
            }
        )
    for deal in deals:
        if deal["stage"] in {"Closed Won", "Closed Lost"}:
            continue
        if deal["health"] in {"at_risk", "stalled"} and deal["value"] >= 300_000:
            items.append(
                {
                    "id": deal["id"],
                    "score": 85 if deal["health"] == "at_risk" else 75,
                    "urgency": "high" if deal["health"] == "at_risk" else "medium",
                    "kind": "deal",
                    "title": deal["situation"],
                    "context": deal["customer_name"],
                    "customer_id": deal["customer_id"],
                    "opportunity_id": deal["id"],
                }
            )
        elif deal["momentum"] == "deteriorating":
            items.append(
                {
                    "id": deal["id"],
                    "score": 70,
                    "urgency": "medium",
                    "kind": "deal",
                    "title": deal["situation"],
                    "context": deal["customer_name"],
                    "customer_id": deal["customer_id"],
                    "opportunity_id": deal["id"],
                }
            )
    items.sort(key=lambda item: (-item["score"], item["title"]))
    seen: set[str] = set()
    ranked: list[dict] = []
    for item in items:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        ranked.append(item)
    return ranked[:6]


def deal_alerts(deals: list[dict], commitments: list[dict]) -> list[dict]:
    overdue_customers = {
        commitment["customer_id"]
        for commitment in commitments
        if commitment["status"] == "Overdue"
    }
    alerts: list[dict] = []
    for deal in deals:
        if deal["stage"] in {"Closed Won", "Closed Lost"}:
            continue
        gap = None
        if deal["simulated_probability"] is not None:
            gap = deal["crm_probability"] - deal["simulated_probability"]
        if deal["health"] == "stalled":
            alerts.append(_alert(deal, "high", "Stalled", deal["situation"]))
        elif deal["health"] == "at_risk":
            alerts.append(_alert(deal, "high", "At risk", deal["situation"]))
        elif deal["momentum"] == "deteriorating":
            alerts.append(_alert(deal, "medium", "Deteriorating", deal["situation"]))
        if deal["customer_id"] in overdue_customers and deal["health"] in {"at_risk", "stalled"}:
            alerts.append(_alert(deal, "high", "Overdue commitment", "A high-priority promise on this account is overdue."))
        if gap is not None and abs(gap) >= 15:
            direction = "above" if gap > 0 else "below"
            alerts.append(
                _alert(
                    deal,
                    "medium",
                    "Forecast gap",
                    f"CRM probability is {abs(gap)} points {direction} the simulated placeholder.",
                )
            )
        if deal["health"] == "stalled" and deal["momentum"] == "unknown":
            alerts.append(
                _alert(
                    deal,
                    "low",
                    "Thin evidence",
                    "There is little recent recorded activity. That is not scored as disengagement.",
                )
            )
    return alerts


def simulated_forecast(deal: dict) -> dict | None:
    if deal["simulated_probability"] is None:
        return None
    return {
        "label": "Simulated placeholder",
        "probability": deal["simulated_probability"],
        "confidence": deal["simulated_confidence"],
        "assumption": deal["simulated_assumption"],
        "disclaimer": SIMULATED_DISCLAIMER,
    }


def _alert(deal: dict, urgency: str, label: str, summary: str) -> dict:
    return {
        "id": f"{deal['id']}-{label}",
        "urgency": urgency,
        "label": label,
        "summary": summary,
        "customer_id": deal["customer_id"],
        "customer_name": deal["customer_name"],
        "opportunity_id": deal["id"],
        "opportunity_name": deal["name"],
    }

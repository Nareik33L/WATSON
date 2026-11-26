"""Phase 1 API behaviour. The portfolio is fictional and nothing here calls out."""

from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from watson.api.app import create_app
from watson.config import ROOT, Settings, assert_loopback
from watson.status import effective_status

NAV_LABELS = [
    "Overview",
    "Daily Briefing",
    "Deals",
    "Customers",
    "Commitments",
    "Approvals",
    "Forecast",
    "Files & Reports",
    "Activity",
    "Integrations",
    "Settings",
]


@pytest.fixture()
def client(tmp_path: Path):
    settings = Settings(
        host="127.0.0.1",
        port=8741,
        open_browser=False,
        database_path=tmp_path / "command.db",
        log_level="WARNING",
        root=ROOT,
        frontend_dist=ROOT / "frontend" / "dist",
        config_files=[],
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
    app.state.engine.dispose()


def test_health_and_meta_are_demo(client: TestClient):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["demo"] is True
    assert health.json()["external_ai"] == "disabled"
    meta = client.get("/api/meta").json()
    assert meta["data_origin"] == "demo"
    assert meta["user"]["name"] == "Alex Harrington"
    assert "No AI model" in meta["disclaimer"]


def test_overview_matches_open_deals(client: TestClient):
    deals = client.get("/api/deals", params={"scope": "all"}).json()["items"]
    open_deals = [deal for deal in deals if deal["stage"] not in {"Closed Won", "Closed Lost"}]
    overview = client.get("/api/overview").json()
    assert overview["demo"] is True
    assert overview["totals"]["open_count"] == len(open_deals) == 9
    assert overview["totals"]["pipeline"] == sum(deal["value"] for deal in open_deals)
    assert overview["totals"]["crm_weighted"] != overview["totals"]["simulated_weighted"]
    assert any(deal["id"] == "opp_meridian_plant" for deal in overview["attention"])


def test_stage_and_owner_and_date_filters(client: TestClient):
    negotiation = client.get("/api/deals", params={"stage": "Negotiation"}).json()["items"]
    assert negotiation
    assert {deal["stage"] for deal in negotiation} == {"Negotiation"}
    everyone = client.get("/api/deals", params={"scope": "all"}).json()["items"]
    assert len(negotiation) < len(everyone)

    jordan = client.get("/api/deals", params={"owner_id": "usr_jordan"}).json()["items"]
    assert {deal["customer_name"] for deal in jordan} == {"Bramble Food Systems", "Harrow Water"}

    far = (date.today() + timedelta(days=400)).isoformat()
    assert client.get("/api/deals", params={"date_from": far}).json()["items"] == []

    midlands = client.get("/api/customers", params={"region": "UK Midlands"}).json()["items"]
    assert midlands
    assert {item["region"] for item in midlands} == {"UK Midlands"}


def test_customer_and_deal_drilldown(client: TestClient):
    customer = client.get("/api/customers/cus_meridian")
    assert customer.status_code == 200
    body = customer.json()
    assert body["customer"]["name"] == "Meridian Fabrication Ltd"
    assert body["customer"]["data_origin"] == "demo"
    assert any(contact["name"] == "Helen Croft" for contact in body["contacts"])
    assert any(deal["id"] == "opp_meridian_plant" for deal in body["opportunities"])
    sources = {item["source_system"] for item in body["timeline"]}
    assert "outlook" in sources
    assert "gong" in sources

    deal = client.get("/api/deals/opp_lumen_fleet").json()
    assert deal["deal"]["value"] == 890_000
    assert deal["simulated_forecast"]["probability"] == 20
    assert "No model was called" in deal["simulated_forecast"]["disclaimer"]
    assert any(person["name"] == "Marcus Hale" and person["active"] is False for person in deal["stakeholders"])
    assert client.get("/api/deals/missing").status_code == 404


def test_commitment_statuses_follow_the_rules(client: TestClient):
    items = {item["id"]: item for item in client.get("/api/commitments").json()["items"]}
    assert items["cmt_meridian_schedule"]["status"] == "Overdue"
    assert items["cmt_meridian_security"]["status"] == "Due today"
    assert items["cmt_ashcombe_intro"]["status"] == "New"
    assert items["cmt_portico_order"]["status"] == "Upcoming"
    assert items["cmt_oak_nda"]["status"] == "Completed"
    assert items["cmt_harrow_workshop"]["status"] == "Cancelled"
    assert items["cmt_bramble_visit"]["status"] == "Unclear"
    assert items["cmt_oak_nda"]["status"] != "Overdue"

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    created = client.post(
        "/api/commitments",
        json={
            "description": "Send the sample pack",
            "customer_id": "cus_kiln",
            "opportunity_id": "opp_kiln_won",
            "owner_name": "Alex Harrington",
            "owner_side": "salesperson",
            "importance": "low",
            "due_date": yesterday,
            "status": "Upcoming",
        },
    )
    assert created.status_code == 201
    assert created.json()["status"] == "Overdue"
    completed = client.patch(
        f"/api/commitments/{created.json()['id']}",
        json={"status": "Completed", "note": "Sent by hand"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "Completed"
    assert completed.json()["completed_at"]


def test_approvals_stay_local(client: TestClient):
    refused = client.post("/api/approvals/apr_meridian_draft/decision", json={"action": "send"})
    assert refused.status_code == 422

    edited = client.post(
        "/api/approvals/apr_lumen_close/decision",
        json={"action": "edit", "proposed_value": "End of the revised month", "note": "Keep it local"},
    )
    assert edited.status_code == 200
    assert edited.json()["status"] == "pending"
    assert edited.json()["proposed_value"] == "End of the revised month"
    assert "No external system" in edited.json()["result_note"]

    deferred = client.post(
        "/api/approvals/apr_vesper_note/decision",
        json={"action": "defer", "note": "After the next conversation"},
    )
    assert deferred.json()["status"] == "deferred"

    rejected = client.post(
        "/api/approvals/apr_excel_lumen/decision",
        json={"action": "reject", "note": "Not this week"},
    )
    assert rejected.json()["status"] == "rejected"

    approved = client.post(
        "/api/approvals/apr_meridian_draft/decision",
        json={"action": "approve", "note": "Record only"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert "No external system" in approved.json()["result_note"]
    assert "never sends an email" in approved.json()["constraint_note"]
    again = client.post("/api/approvals/apr_meridian_draft/decision", json={"action": "approve"})
    assert again.status_code == 409


def test_refresh_and_reset_are_simulated(client: TestClient):
    refreshed = client.post("/api/integrations/gong/refresh")
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["job"]["records_processed"] == 0
    assert "not connected" in body["job"]["message"]
    assert body["integration"]["connected"] is False

    integrations = client.get("/api/integrations").json()
    assert integrations["polling"] is False
    assert {item["key"] for item in integrations["items"]} == {"outlook", "gong", "hubspot", "excel", "calendly"}

    client.post("/api/approvals/apr_ashcombe_contact/decision", json={"action": "approve"})
    reset = client.post("/api/demo/reset")
    assert reset.status_code == 200
    restored = client.get("/api/approvals").json()["items"]
    ashcombe = next(item for item in restored if item["id"] == "apr_ashcombe_contact")
    assert ashcombe["status"] == "pending"


def test_forecast_is_labelled_demo(client: TestClient):
    forecast = client.get("/api/forecast").json()
    assert forecast["label"] == "Demo data"
    assert "No model was called" in forecast["disclaimer"]
    assert forecast["totals"]["pipeline"] > forecast["totals"]["crm_weighted"]
    assert any(item["id"] == "opp_lumen_fleet" for item in forecast["discrepancies"])
    briefing = client.get("/api/briefing").json()
    assert "No model was used" in briefing["method_note"]
    assert briefing["meetings"]
    assert any(meeting["customer_name"] == "Ashcombe Energy" for meeting in briefing["meetings"])


def test_settings_poll_interval_does_not_start_polling(client: TestClient):
    updated = client.patch("/api/settings", json={"poll_interval_minutes": 30})
    assert updated.status_code == 200
    assert updated.json()["poll_interval_minutes"] == 30
    assert "Nothing is polled" in updated.json()["poll_note"]
    assert client.get("/api/integrations").json()["polling"] is False


def test_migration_can_open_the_same_database_twice(tmp_path: Path):
    settings = Settings(
        host="127.0.0.1",
        port=8741,
        open_browser=False,
        database_path=tmp_path / "command.db",
        log_level="WARNING",
        root=ROOT,
        frontend_dist=tmp_path / "dist",
        config_files=[],
    )
    first = create_app(settings)
    first.state.engine.dispose()
    second = create_app(settings)
    with TestClient(second) as test_client:
        customers = test_client.get("/api/customers").json()["items"]
    second.state.engine.dispose()
    assert len(customers) == 11


def test_status_never_completes_from_the_calendar():
    past = date(2026, 1, 1)
    later = date(2026, 6, 1)
    assert effective_status("Completed", past, later) == "Completed"
    assert effective_status("Cancelled", past, later) == "Cancelled"
    assert effective_status("Unclear", past, later) == "Unclear"
    assert effective_status("Upcoming", past, later) == "Overdue"
    assert effective_status("New", date(2026, 6, 2), later) == "New"
    assert effective_status("Upcoming", later, later) == "Due today"


def test_server_refuses_a_public_bind():
    with pytest.raises(SystemExit):
        assert_loopback("0.0.0.0")


def test_services_do_not_call_out():
    banned = ("httpx", "requests", "urllib", "socket", "smtplib", "ollama", "openai", "anthropic")
    root = Path(__file__).resolve().parents[1] / "watson" / "services"
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for word in banned:
            assert word not in text, f"{path.name} mentions {word}"


def test_navigation_labels_exist():
    sidebar = (ROOT / "frontend" / "src" / "components" / "layout" / "nav.ts").read_text(encoding="utf-8")
    for label in NAV_LABELS:
        assert label in sidebar

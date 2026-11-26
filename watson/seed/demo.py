"""Fictional sales portfolio for Helios Industrial.

Every name, company, and figure is invented. Email addresses use example.com.
No row is copied from a real customer. data_origin is always demo.
"""

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

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
    Organisation,
    PortfolioChange,
    Setting,
    SourceReference,
    StoredFile,
    SyncEvent,
    SyncJob,
    User,
)
from watson.tenant import DEMO_ORG_ID

PREFIX = {
    "outlook": "OUT",
    "gong": "GONG",
    "hubspot": "HS",
    "excel": "XLS",
    "calendly": "CAL",
    "manual": "MAN",
}

WIPE_ORDER = (
    AuditRecord,
    SyncEvent,
    SyncJob,
    IntegrationConfig,
    IntelligenceRecord,
    PortfolioChange,
    Approval,
    Commitment,
    Interaction,
    OpportunityContact,
    SourceReference,
    Contact,
    Opportunity,
    StoredFile,
    Integration,
    Setting,
    Customer,
    User,
    Organisation,
)


def seed_demo(session: Session) -> bool:
    if session.get(Organisation, DEMO_ORG_ID) is not None:
        return False
    _load(session)
    return True


def reset_demo(session: Session) -> None:
    org = session.get(Organisation, DEMO_ORG_ID)
    if org is not None and org.data_origin != "demo":
        from watson.errors import AppError

        raise AppError(409, "Refusing to reset a workspace that is not demo data.")
    for model in WIPE_ORDER:
        if model is Organisation:
            session.execute(delete(model).where(model.id == DEMO_ORG_ID))
        else:
            session.execute(delete(model).where(model.organisation_id == DEMO_ORG_ID))
    session.flush()
    _load(session)


def _day(offset: int) -> date:
    return date.today() + timedelta(days=offset)


def _at(offset: int, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(_day(offset), time(hour, minute), tzinfo=timezone.utc)


def _human(value: date) -> str:
    return f"{value.day} {value.strftime('%B %Y')}"


def _load(session: Session) -> None:
    now = datetime.now(timezone.utc)
    session.add(
        Organisation(id=DEMO_ORG_ID, name="Helios Industrial", data_origin="demo", created_at=_at(-120, 9))
    )
    session.flush()
    session.add_all(
        [
            User(
                id="usr_alex",
                organisation_id=DEMO_ORG_ID,
                name="Alex Harrington",
                email="alex.harrington@example.com",
                role="Account executive",
                region="UK South",
                data_origin="demo",
            ),
            User(
                id="usr_jordan",
                organisation_id=DEMO_ORG_ID,
                name="Jordan Ellis",
                email="jordan.ellis@example.com",
                role="Account executive",
                region="UK North",
                data_origin="demo",
            ),
        ]
    )
    session.flush()
    session.add_all(_customers())
    session.flush()
    session.add_all(_contacts())
    session.flush()
    session.add_all(_opportunities())
    session.flush()
    session.add_all(_links())
    session.flush()

    touches = _touches()
    source_ids: dict[str, str] = {}
    pending_interactions = []
    for index, touch in enumerate(touches, start=1):
        source_id = "src_" + touch["id"].removeprefix("int_")
        external = f"DEMO-{PREFIX[touch['system']]}-{1000 + index}"
        source_ids[touch["id"]] = source_id
        when = _at(touch["day"], touch["hour"], touch.get("minute", 0))
        session.add(
            SourceReference(
                id=source_id,
                organisation_id=DEMO_ORG_ID,
                customer_id=touch["customer"],
                opportunity_id=touch["opportunity"],
                source_system=touch["system"],
                external_id=external,
                label=touch["title"],
                occurred_at=when,
                summary=touch["summary"],
                data_origin="demo",
            )
        )
        pending_interactions.append((touch, source_id, when))
    session.flush()
    for touch, source_id, when in pending_interactions:
        session.add(
            Interaction(
                id=touch["id"],
                organisation_id=DEMO_ORG_ID,
                customer_id=touch["customer"],
                opportunity_id=touch["opportunity"],
                source_reference_id=source_id,
                type=touch["type"],
                direction=touch.get("direction"),
                title=touch["title"],
                summary=touch["summary"],
                detail=touch.get("detail", ""),
                occurred_at=when,
                participants=touch["people"],
                data_origin="demo",
            )
        )
    session.flush()

    session.add_all(_commitments(source_ids))
    session.add_all(_approvals())
    session.add_all(_intelligence(source_ids))
    session.add_all(_changes())
    integrations = _integrations()
    session.add_all(integrations)
    session.flush()
    session.add_all(_configs(integrations))
    jobs = _jobs(integrations)
    session.add_all(jobs)
    session.flush()
    session.add_all(_events(jobs))
    session.add_all(_files())
    session.add(
        Setting(
            id="set_poll",
            organisation_id=DEMO_ORG_ID,
            key="poll_interval_minutes",
            value="15",
        )
    )
    session.add(
        AuditRecord(
            id="aud_seed",
            organisation_id=DEMO_ORG_ID,
            actor_user_id="usr_alex",
            actor="Alex Harrington",
            action="seed",
            entity_type="organisation",
            entity_id=DEMO_ORG_ID,
            detail="Fictional portfolio loaded. data_origin=demo. No live sources are connected.",
            created_at=now,
        )
    )
    session.flush()


def _customers() -> list[Customer]:
    rows = [
        ("cus_meridian", "usr_alex", "Meridian Fabrication Ltd", "Industrial machinery", "UK Midlands", "Manufacturing", "strategic", "Multi-site fabricator. Line 2 is the commercial conversation."),
        ("cus_calder", "usr_alex", "Calder & Wren Logistics", "Road freight", "UK North", "Logistics", "core", "Four northern depots. The CFO has joined the pricing conversation."),
        ("cus_ashcombe", "usr_alex", "Ashcombe Energy", "Offshore energy", "Scotland", "Energy", "strategic", "Early discovery on offshore assets. IT joined the evaluation this week."),
        ("cus_portico", "usr_alex", "Portico Retail Group", "Multi-site retail", "UK South", "Retail", "core", "Store audit rollout. Legal redlines are mostly closed."),
        ("cus_vesper", "usr_alex", "Vesper Medical Devices", "Medical devices", "UK East", "Healthcare", "strategic", "Cleanroom line. Regulatory validation is the open objection."),
        ("cus_bramble", "usr_jordan", "Bramble Food Systems", "Food processing", "UK North", "Manufacturing", "emerging", "A single-line pilot. The site visit is not dated."),
        ("cus_lumen", "usr_alex", "Lumen Rail", "Rail freight", "National", "Logistics", "strategic", "Enterprise fleet programme. The original champion has left."),
        ("cus_oak", "usr_alex", "Oak & Iron Construction", "Construction products", "UK South", "Manufacturing", "core", "Quality Suite. The NDA is countersigned."),
        ("cus_harrow", "usr_jordan", "Harrow Water", "Water treatment", "UK Midlands", "Energy", "core", "Three treatment sites were outlined. No next meeting is booked."),
        ("cus_nimbus", "usr_alex", "Nimbus Packaging", "Packaging", "UK North", "Manufacturing", "core", "Closed lost. Kept so the forecast history is visible."),
        ("cus_kiln", "usr_alex", "Kiln & Co", "Ceramics", "UK Midlands", "Manufacturing", "core", "Closed won earlier this month on the demo calendar."),
    ]
    return [
        Customer(
            id=item[0],
            organisation_id=DEMO_ORG_ID,
            owner_id=item[1],
            name=item[2],
            industry=item[3],
            region=item[4],
            business_unit=item[5],
            importance=item[6],
            summary=item[7],
            data_origin="demo",
            created_at=_at(-90, 9),
        )
        for item in rows
    ]


def _contacts() -> list[Contact]:
    rows = [
        ("con_helen", "cus_meridian", "Helen Croft", "Operations Director", "helen.croft@example.com", "01632 960001", True, ""),
        ("con_david", "cus_meridian", "David Okonkwo", "Procurement Lead", "david.okonkwo@example.com", "01632 960002", True, ""),
        ("con_priya", "cus_meridian", "Priya Shah", "Plant Manager", "priya.shah@example.com", "01632 960003", True, ""),
        ("con_tom", "cus_calder", "Tom Adler", "Fleet Director", "tom.adler@example.com", "01632 960004", True, ""),
        ("con_naomi", "cus_calder", "Naomi Briggs", "Chief Financial Officer", "naomi.briggs@example.com", "01632 960005", True, ""),
        ("con_fiona", "cus_ashcombe", "Fiona MacLeod", "Head of Asset Performance", "fiona.macleod@example.com", "01632 960006", True, ""),
        ("con_iain", "cus_ashcombe", "Iain Kerr", "IT Partner", "iain.kerr@example.com", "01632 960007", True, "Joined the evaluation recently."),
        ("con_samir", "cus_portico", "Samir Rahman", "Head of Store Operations", "samir.rahman@example.com", "01632 960008", True, ""),
        ("con_elise", "cus_vesper", "Elise Moreau", "Quality Vice President", "elise.moreau@example.com", "01632 960009", True, ""),
        ("con_ben", "cus_vesper", "Ben Carter", "Regulatory Lead", "ben.carter@example.com", "01632 960010", True, ""),
        ("con_ruth", "cus_bramble", "Ruth Peng", "Site Lead", "ruth.peng@example.com", "01632 960011", True, ""),
        ("con_joelle", "cus_lumen", "Joelle Adeyemi", "Programme Manager", "joelle.adeyemi@example.com", "01632 960012", True, "Interim contact after the champion left."),
        ("con_marcus", "cus_lumen", "Marcus Hale", "Former Programme Director", "marcus.hale@example.com", "01632 960013", False, "Left the business."),
        ("con_pete", "cus_oak", "Pete Lang", "Commercial Manager", "pete.lang@example.com", "01632 960014", True, ""),
        ("con_claire", "cus_harrow", "Claire Dunne", "Transformation Lead", "claire.dunne@example.com", "01632 960015", True, ""),
        ("con_hannah", "cus_nimbus", "Hannah Brooks", "Buying Manager", "hannah.brooks@example.com", "01632 960016", True, ""),
        ("con_owen", "cus_kiln", "Owen Clarke", "Operations Manager", "owen.clarke@example.com", "01632 960017", True, ""),
    ]
    return [
        Contact(
            id=item[0],
            organisation_id=DEMO_ORG_ID,
            customer_id=item[1],
            name=item[2],
            title=item[3],
            email=item[4],
            phone=item[5],
            active=item[6],
            note=item[7],
            data_origin="demo",
        )
        for item in rows
    ]


def _opportunities() -> list[Opportunity]:
    rows = [
        dict(id="opp_meridian_plant", customer="cus_meridian", owner="usr_alex", name="Plant OS — Line 2 rollout", product="Plant OS", pipeline="Enterprise", stage="Negotiation", value=420_000, probability=60, category="Best case", close=22, previous_close=None, previous_stage="Discovery", health="at_risk", momentum="deteriorating", next_step="Send the revised payment schedule.", situation="Procurement is waiting on the revised payment schedule.", last=-2, next_day=None, next_time=None, simulated=45, confidence="limited", assumption="The weekend-cutover requirement is real, and the commercial schedule is overdue."),
        dict(id="opp_calder_fleet", customer="cus_calder", owner="usr_alex", name="Fleet Control — northern depots", product="Fleet Control", pipeline="Mid-Market", stage="Proposal", value=185_000, probability=40, category="Best case", close=47, previous_close=None, previous_stage=None, health="healthy", momentum="improving", next_step="Take a three-year price to the booked follow-up.", situation="The CFO asked about a three-year price. The proposal is out.", last=-1, next_day=6, next_time=(11, 0), simulated=55, confidence="medium", assumption="A pricing question from the CFO is a buying signal, not a forecast."),
        dict(id="opp_ashcombe_energy", customer="cus_ashcombe", owner="usr_alex", name="Energy Monitor — offshore assets", product="Energy Monitor", pipeline="Enterprise", stage="Discovery", value=640_000, probability=20, category="Pipeline", close=94, previous_close=None, previous_stage=None, health="watch", momentum="steady", next_step="Learn Iain Kerr's security criteria in today's meeting.", situation="A new IT stakeholder joins discovery today.", last=-4, next_day=0, next_time=(9, 30), simulated=25, confidence="low", assumption="Discovery is early and a new stakeholder has just joined."),
        dict(id="opp_portico_quality", customer="cus_portico", owner="usr_alex", name="Quality Suite — store audits", product="Quality Suite", pipeline="Mid-Market", stage="Commit", value=96_000, probability=90, category="Commit", close=9, previous_close=None, previous_stage="Proposal", health="healthy", momentum="steady", next_step="Issue the final order form.", situation="Verbal commit. The order form is the remaining step.", last=-2, next_day=0, next_time=(14, 0), simulated=85, confidence="medium", assumption="The remaining step is an order form, with the legal redlines mostly resolved."),
        dict(id="opp_vesper_quality", customer="cus_vesper", owner="usr_alex", name="Plant OS — cleanroom line", product="Plant OS", pipeline="Enterprise", stage="Negotiation", value=310_000, probability=50, category="Best case", close=55, previous_close=None, previous_stage="Proposal", health="at_risk", momentum="deteriorating", next_step="Agree who writes the validation outline.", situation="Regulatory validation is unresolved.", last=-4, next_day=None, next_time=None, simulated=35, confidence="limited", assumption="A validation objection is unresolved. The CRM probability looks high against that."),
        dict(id="opp_bramble_pilot", customer="cus_bramble", owner="usr_jordan", name="Plant OS — single-line pilot", product="Plant OS", pipeline="Mid-Market", stage="Qualification", value=78_000, probability=10, category="Pipeline", close=125, previous_close=None, previous_stage=None, health="watch", momentum="steady", next_step="Agree a date for the site visit.", situation="The site-visit date is unclear.", last=-2, next_day=None, next_time=None, simulated=15, confidence="low", assumption="This is qualification only. The site visit is not dated."),
        dict(id="opp_lumen_fleet", customer="cus_lumen", owner="usr_alex", name="Fleet Control — enterprise programme", product="Fleet Control", pipeline="Enterprise", stage="Proposal", value=890_000, probability=35, category="Pipeline", close=155, previous_close=40, previous_stage="Proposal", health="stalled", momentum="deteriorating", next_step="Confirm who now owns the decision.", situation="The champion has left. The close date in the CRM is no longer credible.", last=-3, next_day=None, next_time=None, simulated=20, confidence="low", assumption="The champion has left. There is not enough evidence to keep the CRM probability."),
        dict(id="opp_oak_quality", customer="cus_oak", owner="usr_alex", name="Quality Suite — plant checks", product="Quality Suite", pipeline="Mid-Market", stage="Commit", value=72_000, probability=80, category="Commit", close=19, previous_close=None, previous_stage="Proposal", health="healthy", momentum="improving", next_step="Collect the signed order form.", situation="The NDA is done. The order form is the remaining step.", last=-6, next_day=None, next_time=None, simulated=75, confidence="medium", assumption="The NDA is complete and there is no open objection in the demo record."),
        dict(id="opp_harrow_energy", customer="cus_harrow", owner="usr_jordan", name="Energy Monitor — treatment sites", product="Energy Monitor", pipeline="Enterprise", stage="Discovery", value=210_000, probability=15, category="Pipeline", close=109, previous_close=None, previous_stage=None, health="stalled", momentum="unknown", next_step="Wait for Claire Dunne to propose a date. Do not chase on silence alone.", situation="No recorded activity for several weeks. That is not treated as disengagement.", last=-26, next_day=None, next_time=None, simulated=18, confidence="low", assumption="There is too little recent evidence to move the probability. Missing activity is not scored as disengagement."),
        dict(id="opp_nimbus_lost", customer="cus_nimbus", owner="usr_alex", name="Plant OS — packing hall", product="Plant OS", pipeline="Mid-Market", stage="Closed Lost", value=128_000, probability=0, category="Omitted", close=-18, previous_close=None, previous_stage="Proposal", health="healthy", momentum="unknown", next_step="No open action.", situation="Closed lost to the fictional competitor Fieldnote.", last=-18, next_day=None, next_time=None, simulated=None, confidence=None, assumption=None),
        dict(id="opp_kiln_won", customer="cus_kiln", owner="usr_alex", name="Quality Suite — kiln line", product="Quality Suite", pipeline="Mid-Market", stage="Closed Won", value=54_000, probability=100, category="Closed", close=-24, previous_close=None, previous_stage="Commit", health="healthy", momentum="steady", next_step="Hand over to delivery.", situation="Closed won on the demo calendar.", last=-24, next_day=None, next_time=None, simulated=None, confidence=None, assumption=None),
    ]
    built: list[Opportunity] = []
    for item in rows:
        next_at = None
        if item["next_day"] is not None and item["next_time"] is not None:
            hour, minute = item["next_time"]
            next_at = _at(item["next_day"], hour, minute)
        built.append(
            Opportunity(
                id=item["id"],
                organisation_id=DEMO_ORG_ID,
                customer_id=item["customer"],
                owner_id=item["owner"],
                name=item["name"],
                product=item["product"],
                pipeline=item["pipeline"],
                stage=item["stage"],
                value=item["value"],
                currency="GBP",
                crm_probability=item["probability"],
                forecast_category=item["category"],
                close_date=_day(item["close"]),
                previous_close_date=None if item["previous_close"] is None else _day(item["previous_close"]),
                previous_stage=item["previous_stage"],
                health=item["health"],
                momentum=item["momentum"],
                next_step=item["next_step"],
                situation=item["situation"],
                last_interaction_at=_at(item["last"], 12),
                next_interaction_at=next_at,
                simulated_probability=item["simulated"],
                simulated_confidence=item["confidence"],
                simulated_assumption=item["assumption"],
                data_origin="demo",
                created_at=_at(-70, 9),
                updated_at=_at(item["last"], 12),
            )
        )
    return built


def _links() -> list[OpportunityContact]:
    rows = [
        ("ocl_helen", "opp_meridian_plant", "con_helen", "champion", True),
        ("ocl_david", "opp_meridian_plant", "con_david", "economic buyer", False),
        ("ocl_priya", "opp_meridian_plant", "con_priya", "user", False),
        ("ocl_tom", "opp_calder_fleet", "con_tom", "champion", True),
        ("ocl_naomi", "opp_calder_fleet", "con_naomi", "economic buyer", False),
        ("ocl_fiona", "opp_ashcombe_energy", "con_fiona", "champion", True),
        ("ocl_iain", "opp_ashcombe_energy", "con_iain", "influencer", False),
        ("ocl_samir", "opp_portico_quality", "con_samir", "economic buyer", True),
        ("ocl_elise", "opp_vesper_quality", "con_elise", "champion", True),
        ("ocl_ben", "opp_vesper_quality", "con_ben", "blocker", False),
        ("ocl_ruth", "opp_bramble_pilot", "con_ruth", "champion", True),
        ("ocl_joelle", "opp_lumen_fleet", "con_joelle", "influencer", True),
        ("ocl_marcus", "opp_lumen_fleet", "con_marcus", "former champion", False),
        ("ocl_pete", "opp_oak_quality", "con_pete", "economic buyer", True),
        ("ocl_claire", "opp_harrow_energy", "con_claire", "champion", True),
        ("ocl_hannah", "opp_nimbus_lost", "con_hannah", "economic buyer", True),
        ("ocl_owen", "opp_kiln_won", "con_owen", "champion", True),
    ]
    return [
        OpportunityContact(
            id=item[0],
            organisation_id=DEMO_ORG_ID,
            opportunity_id=item[1],
            contact_id=item[2],
            role=item[3],
            is_primary=item[4],
        )
        for item in rows
    ]


def _touches() -> list[dict]:
    return [
        dict(id="int_meridian_profiles", customer="cus_meridian", opportunity="opp_meridian_plant", system="outlook", type="email", direction="outbound", title="Payment profiles sent", summary="Alex sent three payment profiles for the Line 2 rollout. The email body is not stored.", day=-20, hour=9, people=["Alex Harrington", "David Okonkwo"]),
        dict(id="int_meridian_downtime", customer="cus_meridian", opportunity="opp_meridian_plant", system="gong", type="call", title="Line 2 downtime discussed", summary="Helen Croft said a weekday shutdown would not be accepted. David Okonkwo asked about penalty clauses.", day=-12, hour=15, people=["Alex Harrington", "Helen Croft", "David Okonkwo", "Priya Shah"]),
        dict(id="int_meridian_cutover", customer="cus_meridian", opportunity="opp_meridian_plant", system="outlook", type="email", direction="inbound", title="Weekend cutover confirmed", summary="Helen confirmed weekend cutover is a requirement. This is a change from the earlier discovery notes.", day=-6, hour=11, people=["Helen Croft", "Alex Harrington"]),
        dict(id="int_meridian_stage", customer="cus_meridian", opportunity="opp_meridian_plant", system="hubspot", type="crm", title="Stage set to Negotiation", summary="HubSpot stage moved from Discovery to Negotiation after the commercial discussion.", day=-2, hour=16, people=["Alex Harrington"]),
        dict(id="int_calder_scope", customer="cus_calder", opportunity="opp_calder_fleet", system="gong", type="call", title="Depot scope and pricing", summary="Naomi Briggs asked whether a three-year term would change the price. Tom Adler confirmed four depots are in scope.", day=-9, hour=10, people=["Alex Harrington", "Tom Adler", "Naomi Briggs"]),
        dict(id="int_calder_booked", customer="cus_calder", opportunity="opp_calder_fleet", system="calendly", type="booking", title="Follow-up booked", summary="A follow-up was booked. The event description is not stored.", day=-3, hour=8, people=["Naomi Briggs", "Alex Harrington"]),
        dict(id="int_calder_proposal", customer="cus_calder", opportunity="opp_calder_fleet", system="outlook", type="email", direction="outbound", title="Proposal sent", summary="Alex sent the Fleet Control proposal. The email body is not stored.", day=-1, hour=13, people=["Alex Harrington", "Tom Adler", "Naomi Briggs"]),
        dict(id="int_calder_followup", customer="cus_calder", opportunity="opp_calder_fleet", system="calendly", type="meeting", title="Follow-up with Calder & Wren", summary="Scheduled follow-up to walk through the three-year option.", detail="Walk Naomi through a three-year option. Tom Adler owns the depot scope.", day=6, hour=11, people=["Naomi Briggs", "Tom Adler", "Alex Harrington"]),
        dict(id="int_ashcombe_intro", customer="cus_ashcombe", opportunity="opp_ashcombe_energy", system="outlook", type="meeting", title="Introductory meeting", summary="Fiona MacLeod described the offshore asset scope. No commercial proposal was requested.", day=-15, hour=10, people=["Fiona MacLeod", "Alex Harrington"]),
        dict(id="int_ashcombe_it", customer="cus_ashcombe", opportunity="opp_ashcombe_energy", system="outlook", type="email", direction="inbound", title="IT stakeholder added", summary="Fiona said Iain Kerr will join the evaluation for security and integration.", day=-4, hour=9, people=["Fiona MacLeod", "Alex Harrington"]),
        dict(id="int_ashcombe_today", customer="cus_ashcombe", opportunity="opp_ashcombe_energy", system="outlook", type="meeting", title="Discovery with Ashcombe Energy", summary="Discovery meeting with Fiona MacLeod and Iain Kerr.", detail="Ask Iain what would block a security review. Confirm Fiona still owns the operational decision. Do not present a price.", day=0, hour=9, minute=30, people=["Fiona MacLeod", "Iain Kerr", "Alex Harrington"]),
        dict(id="int_portico_proposal", customer="cus_portico", opportunity="opp_portico_quality", system="hubspot", type="crm", title="Proposal recorded", summary="Quality Suite proposal logged against Portico Retail Group.", day=-18, hour=12, people=["Alex Harrington"]),
        dict(id="int_portico_redlines", customer="cus_portico", opportunity="opp_portico_quality", system="outlook", type="email", direction="inbound", title="Legal redlines reduced", summary="Samir Rahman said the remaining redlines are commercial, not legal.", day=-7, hour=15, people=["Samir Rahman", "Alex Harrington"]),
        dict(id="int_portico_commit", customer="cus_portico", opportunity="opp_portico_quality", system="hubspot", type="crm", title="Moved to Commit", summary="Stage moved to Commit. Forecast category set to Commit.", day=-2, hour=17, people=["Alex Harrington"]),
        dict(id="int_portico_today", customer="cus_portico", opportunity="opp_portico_quality", system="outlook", type="meeting", title="Order form review", summary="Review the final order form with Samir Rahman.", detail="Take the order form. Confirm the remaining redlines are commercial. The open promise is to issue that form.", day=0, hour=14, people=["Samir Rahman", "Alex Harrington"]),
        dict(id="int_vesper_objection", customer="cus_vesper", opportunity="opp_vesper_quality", system="gong", type="call", title="Validation objection", summary="Ben Carter said the cleanroom line cannot move to Commit without a validation outline. Elise Moreau agreed.", day=-11, hour=14, people=["Alex Harrington", "Elise Moreau", "Ben Carter"]),
        dict(id="int_vesper_outline", customer="cus_vesper", opportunity="opp_vesper_quality", system="outlook", type="email", direction="inbound", title="Validation outline requested", summary="Elise asked Alex for a validation pack outline. The email body is not stored.", day=-4, hour=11, people=["Elise Moreau", "Alex Harrington"]),
        dict(id="int_lumen_checkin", customer="cus_lumen", opportunity="opp_lumen_fleet", system="gong", type="call", title="Programme check-in", summary="Marcus Hale described the depot rollout as a priority for this financial year.", day=-30, hour=10, people=["Alex Harrington", "Marcus Hale"]),
        dict(id="int_lumen_left", customer="cus_lumen", opportunity="opp_lumen_fleet", system="outlook", type="email", direction="inbound", title="Champion has left", summary="Joelle Adeyemi wrote that Marcus Hale has left the business and asked for time to re-brief.", day=-8, hour=9, people=["Joelle Adeyemi", "Alex Harrington"]),
        dict(id="int_lumen_crm", customer="cus_lumen", opportunity="opp_lumen_fleet", system="hubspot", type="crm", title="Close date not moved", summary="The CRM close date was not moved when the champion left.", day=-8, hour=9, minute=30, people=["Alex Harrington"]),
        dict(id="int_lumen_sheet", customer="cus_lumen", opportunity="opp_lumen_fleet", system="excel", type="spreadsheet", title="Forecast sheet disagrees", summary="The Q4 workbook still shows Lumen in the old close month.", day=-3, hour=16, people=["Alex Harrington"]),
        dict(id="int_oak_nda", customer="cus_oak", opportunity="opp_oak_quality", system="outlook", type="email", direction="outbound", title="Order form and NDA", summary="Alex sent the NDA and a draft order form. The attachments are not stored.", day=-14, hour=11, people=["Alex Harrington", "Pete Lang"]),
        dict(id="int_oak_signed", customer="cus_oak", opportunity="opp_oak_quality", system="hubspot", type="crm", title="NDA countersigned", summary="Pete Lang confirmed the NDA is countersigned.", day=-6, hour=15, people=["Pete Lang", "Alex Harrington"]),
        dict(id="int_harrow_intro", customer="cus_harrow", opportunity="opp_harrow_energy", system="outlook", type="meeting", title="Transformation introduction", summary="Claire Dunne outlined three treatment sites. No next meeting was booked.", day=-26, hour=11, people=["Claire Dunne", "Jordan Ellis"]),
        dict(id="int_bramble_enquiry", customer="cus_bramble", opportunity="opp_bramble_pilot", system="outlook", type="email", direction="inbound", title="Pilot enquiry", summary="Ruth Peng asked for a short pilot on one line. A date for a site visit was left open.", day=-2, hour=10, people=["Ruth Peng", "Jordan Ellis"]),
        dict(id="int_kiln_won", customer="cus_kiln", opportunity="opp_kiln_won", system="hubspot", type="crm", title="Closed won", summary="Quality Suite marked Closed Won.", day=-24, hour=16, people=["Owen Clarke", "Alex Harrington"]),
        dict(id="int_nimbus_lost", customer="cus_nimbus", opportunity="opp_nimbus_lost", system="hubspot", type="crm", title="Closed lost", summary="Lost to the fictional competitor Fieldnote on price. No transcript is stored.", day=-18, hour=12, people=["Hannah Brooks", "Alex Harrington"]),
    ]


def _commitments(source_ids: dict[str, str]) -> list[Commitment]:
    rows = [
        ("cmt_meridian_schedule", "cus_meridian", "opp_meridian_plant", "int_meridian_downtime", "Alex Harrington", "salesperson", "Send the revised payment schedule to David Okonkwo.", "high", "Upcoming", -2, "Raised on the downtime call.", "Do this before asking procurement for a new date."),
        ("cmt_meridian_security", "cus_meridian", "opp_meridian_plant", "int_meridian_cutover", "David Okonkwo", "customer", "Return the security questionnaire.", "high", "Upcoming", 0, "Helen tied this to the weekend-cutover requirement.", ""),
        ("cmt_portico_order", "cus_portico", "opp_portico_quality", "int_portico_commit", "Alex Harrington", "salesperson", "Issue the final order form to Samir Rahman.", "high", "Upcoming", 3, "Stage is Commit. The form is the remaining step.", ""),
        ("cmt_ashcombe_intro", "cus_ashcombe", "opp_ashcombe_energy", "int_ashcombe_it", "Alex Harrington", "salesperson", "Introduce Iain Kerr to the solutions consultant.", "medium", "New", 1, "Fiona added Iain to the evaluation.", ""),
        ("cmt_vesper_pack", "cus_vesper", "opp_vesper_quality", "int_vesper_outline", "Elise Moreau", "customer", "Share the validation pack outline.", "high", "Upcoming", 6, "Requested after the validation objection.", ""),
        ("cmt_lumen_buyer", "cus_lumen", "opp_lumen_fleet", "int_lumen_left", "Alex Harrington", "salesperson", "Confirm whether Joelle Adeyemi is the economic buyer.", "high", "Upcoming", -5, "The previous champion has left the business.", ""),
        ("cmt_calder_price", "cus_calder", "opp_calder_fleet", "int_calder_scope", "Alex Harrington", "salesperson", "Prepare a three-year pricing option for Naomi Briggs.", "medium", "Upcoming", 8, "Naomi asked about a multi-year price on the scoping call.", ""),
        ("cmt_bramble_visit", "cus_bramble", "opp_bramble_pilot", "int_bramble_enquiry", "Ruth Peng", "customer", "Site visit date was discussed and not agreed.", "low", "Unclear", 14, "The enquiry left the date open.", ""),
        ("cmt_oak_nda", "cus_oak", "opp_oak_quality", "int_oak_signed", "Alex Harrington", "salesperson", "Countersign the NDA.", "medium", "Completed", -10, "Pete Lang confirmed the countersignature.", ""),
        ("cmt_harrow_workshop", "cus_harrow", "opp_harrow_energy", "int_harrow_intro", "Jordan Ellis", "salesperson", "November workshop with Harrow Water.", "low", "Cancelled", -20, "The customer cancelled the workshop.", ""),
        ("cmt_vesper_followup", "cus_vesper", "opp_vesper_quality", "int_vesper_objection", "Alex Harrington", "salesperson", "Book a regulatory follow-up with Ben Carter.", "high", "Upcoming", 0, "Ben owns the regulatory objection.", ""),
        ("cmt_nimbus_note", "cus_nimbus", "opp_nimbus_lost", "int_nimbus_lost", "Alex Harrington", "salesperson", "Record the lost-deal note.", "low", "Completed", -18, "Lost to Fieldnote on price.", ""),
    ]
    built = []
    for item in rows:
        stored = item[8]
        due_offset = item[9]
        completed_at = _at(due_offset + 4, 15) if stored == "Completed" else None
        built.append(
            Commitment(
                id=item[0],
                organisation_id=DEMO_ORG_ID,
                customer_id=item[1],
                opportunity_id=item[2],
                source_reference_id=source_ids.get(item[3]),
                owner_name=item[4],
                owner_side=item[5],
                description=item[6],
                importance=item[7],
                status=stored,
                due_date=_day(due_offset),
                evidence=item[10],
                related_recommendation=item[11],
                completion_note="Confirmed complete in the demo record." if stored == "Completed" else "",
                created_at=_at(due_offset - 5, 9),
                completed_at=completed_at,
                data_origin="demo",
            )
        )
    return built


def _approvals() -> list[Approval]:
    lumen_old = _human(_day(40))
    lumen_new = _human(_day(155))
    common = dict(organisation_id=DEMO_ORG_ID, revision=1, data_origin="demo", decided_by=None, decided_at=None, decision_note="")
    return [
        Approval(
            id="apr_lumen_close",
            customer_id="cus_lumen",
            opportunity_id="opp_lumen_fleet",
            source_reference_id="src_lumen_left",
            action_type="hubspot_field",
            title="Update the Lumen close date",
            proposed_action="Change the HubSpot close date on Fleet Control — enterprise programme.",
            source_system="hubspot",
            current_value=lumen_old,
            proposed_value=lumen_new,
            reason="Joelle Adeyemi said the original champion has left and asked for time to re-brief. The old close date is no longer credible.",
            evidence="DEMO reference on the email 'Champion has left', plus the workbook row that still uses the old month.",
            impact="The CRM forecast would move with the situation. The workbook would still be wrong until a new version is approved separately.",
            constraint_note="Approving stores your decision locally. It does not change HubSpot.",
            status="pending",
            created_at=_at(-1, 8),
            **common,
        ),
        Approval(
            id="apr_vesper_note",
            customer_id="cus_vesper",
            opportunity_id="opp_vesper_quality",
            source_reference_id="src_vesper_objection",
            action_type="crm_note",
            title="Record the validation objection",
            proposed_action="Add a CRM note that validation is a condition of Commit.",
            source_system="hubspot",
            current_value="No note on the objection.",
            proposed_value="Validation pack is a condition of moving to Commit. Ben Carter owns the regulatory review. Do not treat silence as agreement.",
            reason="The objection is on the call record and missing from the CRM.",
            evidence="Gong call 'Validation objection'.",
            impact="The next person who opens the deal can see the objection without the transcript.",
            constraint_note="Approving stores your decision locally. It does not write a note to HubSpot.",
            status="pending",
            created_at=_at(-1, 9),
            **common,
        ),
        Approval(
            id="apr_meridian_draft",
            customer_id="cus_meridian",
            opportunity_id="opp_meridian_plant",
            source_reference_id="src_meridian_cutover",
            action_type="outlook_draft",
            title="Prepare a follow-up for Helen Croft",
            proposed_action="Prepare a short draft to Helen Croft. Do not send it.",
            source_system="outlook",
            current_value="No draft.",
            proposed_value="A short note offering two weekend cutover windows and the revised payment profile. No full email body is stored.",
            reason="Helen confirmed weekend cutover, and the payment schedule Alex promised is overdue.",
            evidence="Outlook reference 'Weekend cutover confirmed'.",
            impact="Gives Alex a starting point for a draft in a later phase. Nothing is sent.",
            constraint_note="Approving does not create an Outlook draft and never sends an email.",
            status="pending",
            created_at=_at(-1, 10),
            **common,
        ),
        Approval(
            id="apr_excel_lumen",
            customer_id="cus_lumen",
            opportunity_id="opp_lumen_fleet",
            source_reference_id="src_lumen_sheet",
            action_type="excel_version",
            title="New version of the Q4 forecast workbook",
            proposed_action="Create a new workbook version that moves Lumen to the later close month.",
            source_system="excel",
            current_value="Q4_enterprise_forecast.xlsm still shows the old Lumen month.",
            proposed_value="A new file, Q4_enterprise_forecast_v2.xlsx, with Lumen moved. The original file would stay untouched.",
            reason="The sheet and the situation on the account disagree.",
            evidence="Spreadsheet event 'Forecast sheet disagrees'.",
            impact="A later phase could write that new file. This prototype does not.",
            constraint_note="Approving does not create a workbook and does not touch an original file.",
            status="pending",
            created_at=_at(-1, 11),
            **common,
        ),
        Approval(
            id="apr_ashcombe_contact",
            customer_id="cus_ashcombe",
            opportunity_id="opp_ashcombe_energy",
            source_reference_id="src_ashcombe_it",
            action_type="contact_update",
            title="Add Iain Kerr to the account",
            proposed_action="Add Iain Kerr, IT Partner, as a stakeholder on Ashcombe Energy.",
            source_system="hubspot",
            current_value="Iain Kerr is not on the CRM account.",
            proposed_value="Iain Kerr, IT Partner, influencer on Energy Monitor.",
            reason="Fiona said he will join the security and integration review.",
            evidence="Outlook reference 'IT stakeholder added'.",
            impact="The buying group on the CRM would match the meeting today.",
            constraint_note="Approving stores your decision locally. It does not update HubSpot.",
            status="pending",
            created_at=_at(0, 7),
            **common,
        ),
        Approval(
            id="apr_calder_assoc",
            customer_id="cus_calder",
            opportunity_id="opp_calder_fleet",
            source_reference_id="src_calder_scope",
            action_type="association",
            title="Associate Naomi Briggs as economic buyer",
            proposed_action="Link Naomi Briggs to the Fleet Control deal as economic buyer.",
            source_system="hubspot",
            current_value="Naomi is a contact and is not an economic buyer on the deal.",
            proposed_value="Naomi Briggs, economic buyer.",
            reason="She asked the pricing question. Wait until the booked follow-up before writing it to the CRM.",
            evidence="Gong call 'Depot scope and pricing'.",
            impact="Makes the buying group explicit. Deferred on purpose.",
            constraint_note="Approving would still only be local. This item is deferred and has not been approved.",
            status="deferred",
            decision_note="Wait until after the booked follow-up.",
            decided_by="Alex Harrington",
            decided_at=_at(-2, 18),
            created_at=_at(-3, 12),
            organisation_id=DEMO_ORG_ID,
            revision=1,
            data_origin="demo",
        ),
        Approval(
            id="apr_oak_note",
            customer_id="cus_oak",
            opportunity_id="opp_oak_quality",
            source_reference_id="src_oak_signed",
            action_type="crm_note",
            title="Note that the NDA is countersigned",
            proposed_action="Add a CRM note that the Oak & Iron NDA is countersigned.",
            source_system="hubspot",
            current_value="NDA status blank.",
            proposed_value="NDA countersigned by Pete Lang.",
            reason="Pete confirmed it.",
            evidence="HubSpot activity 'NDA countersigned'.",
            impact="Removes a repeated question from the next review. The demo records this as already decided.",
            constraint_note="The recorded approval did not write to HubSpot. It only exists in this local database.",
            status="approved",
            decision_note="Recorded locally in the demo history.",
            decided_by="Alex Harrington",
            decided_at=_at(-6, 16),
            created_at=_at(-6, 15),
            organisation_id=DEMO_ORG_ID,
            revision=1,
            data_origin="demo",
        ),
        Approval(
            id="apr_harrow_chase",
            customer_id="cus_harrow",
            opportunity_id="opp_harrow_energy",
            source_reference_id="src_harrow_intro",
            action_type="commitment_update",
            title="Do not open an automatic chase",
            proposed_action="Create a chase task because Harrow has gone quiet.",
            source_system="manual",
            current_value="No chase task.",
            proposed_value="Automatic chase task.",
            reason="The only recorded meeting was several weeks ago.",
            evidence="One introductory meeting. No cancellation and no complaint.",
            impact="A chase would treat missing notes as disengagement. That rule is rejected.",
            constraint_note="Rejection is local. No task was created in any other system.",
            status="rejected",
            decision_note="No recorded activity is not the same as disengagement. Do not chase on that basis.",
            decided_by="Jordan Ellis",
            decided_at=_at(-5, 11),
            created_at=_at(-6, 9),
            organisation_id=DEMO_ORG_ID,
            revision=1,
            data_origin="demo",
        ),
    ]


def _intelligence(source_ids: dict[str, str]) -> list[IntelligenceRecord]:
    rows = [
        ("intel_meridian_decision", "cus_meridian", "opp_meridian_plant", "int_meridian_cutover", "decision", "Weekend cutover is a requirement.", "demo_record", -6),
        ("intel_meridian_risk", "cus_meridian", "opp_meridian_plant", "int_meridian_downtime", "risk", "A weekday shutdown was rejected by the plant.", "demo_record", -12),
        ("intel_meridian_rec", "cus_meridian", "opp_meridian_plant", "int_meridian_profiles", "recommendation", "Send the revised payment schedule before asking procurement for a date.", "simulated_placeholder", -1),
        ("intel_calder_signal", "cus_calder", "opp_calder_fleet", "int_calder_scope", "buying_signal", "The CFO asked about a three-year price.", "demo_record", -9),
        ("intel_calder_rec", "cus_calder", "opp_calder_fleet", "int_calder_booked", "recommendation", "Take a priced three-year option to the booked follow-up.", "simulated_placeholder", -1),
        ("intel_vesper_risk", "cus_vesper", "opp_vesper_quality", "int_vesper_objection", "risk", "A validation outline is a condition of Commit.", "demo_record", -11),
        ("intel_vesper_rec", "cus_vesper", "opp_vesper_quality", "int_vesper_objection", "recommendation", "Do not advance the CRM stage until the outline exists.", "simulated_placeholder", -1),
        ("intel_lumen_risk", "cus_lumen", "opp_lumen_fleet", "int_lumen_left", "risk", "The economic buyer is unconfirmed after the champion left.", "demo_record", -8),
        ("intel_lumen_rec", "cus_lumen", "opp_lumen_fleet", "int_lumen_left", "recommendation", "Hold the forecast until Joelle confirms who decides.", "simulated_placeholder", -1),
        ("intel_harrow_risk", "cus_harrow", "opp_harrow_energy", "int_harrow_intro", "risk", "No recorded activity since the introduction. Do not score this as disengagement.", "demo_record", -26),
        ("intel_harrow_rec", "cus_harrow", "opp_harrow_energy", "int_harrow_intro", "recommendation", "Leave the probability where it is until there is new evidence.", "simulated_placeholder", -1),
        ("intel_ashcombe_decision", "cus_ashcombe", "opp_ashcombe_energy", "int_ashcombe_it", "decision", "IT will be part of the evaluation.", "demo_record", -4),
        ("intel_ashcombe_rec", "cus_ashcombe", "opp_ashcombe_energy", "int_ashcombe_today", "recommendation", "Use today's meeting to learn Iain's security criteria.", "simulated_placeholder", 0),
        ("intel_portico_signal", "cus_portico", "opp_portico_quality", "int_portico_redlines", "buying_signal", "The remaining redlines are commercial, not legal.", "demo_record", -7),
        ("intel_portico_rec", "cus_portico", "opp_portico_quality", "int_portico_today", "recommendation", "Bring the order form to today's meeting.", "simulated_placeholder", 0),
    ]
    return [
        IntelligenceRecord(
            id=item[0],
            organisation_id=DEMO_ORG_ID,
            customer_id=item[1],
            opportunity_id=item[2],
            source_reference_id=source_ids.get(item[3]),
            category=item[4],
            summary=item[5],
            producer=item[6],
            data_origin="demo",
            created_at=_at(item[7], 12),
        )
        for item in rows
    ]


def _changes() -> list[PortfolioChange]:
    rows = [
        ("chg_lumen_date", "cus_lumen", "opp_lumen_fleet", "outlook", "close_date", "The close date is no longer credible after the champion left.", -8),
        ("chg_ashcombe_stake", "cus_ashcombe", "opp_ashcombe_energy", "outlook", "stakeholder", "Iain Kerr joined the evaluation.", -4),
        ("chg_meridian_req", "cus_meridian", "opp_meridian_plant", "outlook", "requirement", "Weekend cutover was confirmed as a requirement.", -6),
        ("chg_calder_book", "cus_calder", "opp_calder_fleet", "calendly", "meeting", "A follow-up was booked.", -3),
        ("chg_vesper_risk", "cus_vesper", "opp_vesper_quality", "gong", "risk", "A validation objection was recorded.", -11),
        ("chg_portico_stage", "cus_portico", "opp_portico_quality", "hubspot", "stage", "Stage moved to Commit.", -2),
        ("chg_oak_nda", "cus_oak", "opp_oak_quality", "hubspot", "commitment", "The NDA was countersigned.", -6),
        ("chg_bramble_new", "cus_bramble", "opp_bramble_pilot", "outlook", "enquiry", "A pilot enquiry arrived.", -2),
        ("chg_lumen_sheet", "cus_lumen", "opp_lumen_fleet", "excel", "forecast", "The workbook close month disagrees with the situation on the account.", -3),
    ]
    return [
        PortfolioChange(
            id=item[0],
            organisation_id=DEMO_ORG_ID,
            customer_id=item[1],
            opportunity_id=item[2],
            source_system=item[3],
            kind=item[4],
            summary=item[5],
            occurred_at=_at(item[6], 15),
            data_origin="demo",
        )
        for item in rows
    ]


def _integrations() -> list[Integration]:
    rows = [
        ("intg_outlook", "outlook", "Microsoft Outlook", "Email metadata and calendar meetings. Bodies and threads are not stored.", -26),
        ("intg_gong", "gong", "Gong", "Call metadata. A later phase may process a transcript temporarily. Recordings are not stored.", -20),
        ("intg_hubspot", "hubspot", "HubSpot", "CRM reference for companies, contacts, and deals. Updates need approval and are not live.", -14),
        ("intg_excel", "excel", "Excel", "Workbook inspection is a later phase. An original file is never overwritten.", -8),
        ("intg_calendly", "calendly", "Calendly", "Booking metadata. Questionnaires and raw payloads are not stored.", -3),
    ]
    return [
        Integration(
            id=item[0],
            organisation_id=DEMO_ORG_ID,
            key=item[1],
            name=item[2],
            summary=item[3],
            status="simulated",
            mode="simulated",
            last_sync_at=_at(item[4], 7),
            records_seen=0,
            data_origin="demo",
        )
        for item in rows
    ]


def _configs(integrations: list[Integration]) -> list[IntegrationConfig]:
    return [
        IntegrationConfig(
            id=f"cfg_{integration.key}",
            organisation_id=DEMO_ORG_ID,
            integration_id=integration.id,
            poll_interval_minutes=15,
            enabled=False,
            notes="Stored for a later phase. Polling is not running.",
        )
        for integration in integrations
    ]


def _jobs(integrations: list[Integration]) -> list[SyncJob]:
    return [
        SyncJob(
            id=f"job_seed_{integration.key}",
            organisation_id=DEMO_ORG_ID,
            integration_id=integration.id,
            status="simulated_success",
            trigger="scheduled",
            records_processed=0,
            message="Sample history written by the demo dataset. No external system was contacted.",
            started_at=integration.last_sync_at,
            finished_at=integration.last_sync_at,
        )
        for integration in integrations
    ]


def _events(jobs: list[SyncJob]) -> list[SyncEvent]:
    return [
        SyncEvent(
            id=f"evt_{job.id}",
            organisation_id=DEMO_ORG_ID,
            integration_id=job.integration_id,
            sync_job_id=job.id,
            event_type="demo_history",
            status="simulated_success",
            message=job.message,
            dedup_key=job.id,
            occurred_at=job.started_at,
        )
        for job in jobs
    ]


def _files() -> list[StoredFile]:
    return [
        StoredFile(
            id="file_q4",
            organisation_id=DEMO_ORG_ID,
            name="Q4_enterprise_forecast.xlsm",
            kind="workbook",
            row_count=42,
            issue_count=3,
            summary="Fictional Q4 forecast sheet. The original file is not in this prototype.",
            findings=[
                "Lumen Rail's close month on the sheet is earlier than the situation on the account.",
                "Nimbus Packaging is still listed as open. The demo CRM has it Closed Lost.",
                "Meridian Fabrication Ltd matches the CRM value of £420,000.",
            ],
            uploaded_at=_at(-3, 16),
            data_origin="demo",
        ),
        StoredFile(
            id="file_accounts",
            organisation_id=DEMO_ORG_ID,
            name="named_accounts.xlsx",
            kind="workbook",
            row_count=18,
            issue_count=1,
            summary="Fictional account list used to illustrate a duplicate name.",
            findings=["'Harrow Water' and 'Harrow Water Ltd' look like the same account."],
            uploaded_at=_at(-20, 11),
            data_origin="demo",
        ),
        StoredFile(
            id="file_weekly",
            organisation_id=DEMO_ORG_ID,
            name="Weekly pipeline briefing",
            kind="report",
            row_count=0,
            issue_count=0,
            summary="A preview of the weekly briefing. Export is not available in this prototype.",
            findings=["Built from the same fictional portfolio as the dashboard."],
            uploaded_at=_at(0, 6),
            data_origin="demo",
        ),
    ]


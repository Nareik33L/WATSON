"""Integration status.

Phase 1 keeps a card and a configuration row per source. Refresh is handled
by the sync service and never opens a connection.
"""

from sqlalchemy.orm import Session

from watson.db.models import IntegrationConfig, Setting
from watson.services.audit import new_id


def poll_interval(session: Session, organisation_id: str) -> int:
    from watson.dal.queries import get_setting

    raw = get_setting(session, organisation_id, "poll_interval_minutes", "15")
    try:
        value = int(raw)
    except ValueError:
        return 15
    return min(1440, max(5, value))


def save_poll_interval(session: Session, organisation_id: str, minutes: int) -> int:
    from sqlalchemy import select

    statement = select(Setting).where(
        Setting.organisation_id == organisation_id,
        Setting.key == "poll_interval_minutes",
    )
    row = session.scalars(statement).first()
    if row is None:
        session.add(
            Setting(
                id=new_id("set"),
                organisation_id=organisation_id,
                key="poll_interval_minutes",
                value=str(minutes),
            )
        )
    else:
        row.value = str(minutes)
    configs = session.scalars(
        select(IntegrationConfig).where(IntegrationConfig.organisation_id == organisation_id)
    )
    for config in configs:
        config.poll_interval_minutes = minutes
        config.enabled = False
        config.notes = "Stored for a later phase. Polling is not running."
    return minutes

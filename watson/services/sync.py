"""Simulated sync.

A manual refresh writes a local job and a local event. It does not fetch
mail, calls, CRM rows, workbooks, or bookings. Failed jobs are kept; this
path does not delete them.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from watson.db.models import Integration, SyncEvent, SyncJob
from watson.services.audit import new_id, record

log = logging.getLogger("watson.sync")

REFRESH_MESSAGE = (
    "Simulated refresh finished locally. No records were fetched because this integration is not connected."
)


def manual_refresh(
    session: Session,
    integration: Integration,
    *,
    actor: str,
    actor_user_id: str | None,
) -> SyncJob:
    now = datetime.now(timezone.utc)
    job = SyncJob(
        id=new_id("job"),
        organisation_id=integration.organisation_id,
        integration_id=integration.id,
        status="simulated_success",
        trigger="manual",
        records_processed=0,
        message=REFRESH_MESSAGE,
        started_at=now,
        finished_at=now,
    )
    session.add(job)
    session.flush()
    session.add(
        SyncEvent(
            id=new_id("evt"),
            organisation_id=integration.organisation_id,
            integration_id=integration.id,
            sync_job_id=job.id,
            event_type="manual_refresh",
            status="simulated_success",
            message=REFRESH_MESSAGE,
            dedup_key=job.id,
            occurred_at=now,
        )
    )
    integration.last_sync_at = now
    record(
        session,
        organisation_id=integration.organisation_id,
        actor=actor,
        actor_user_id=actor_user_id,
        action="refresh",
        entity_type="integration",
        entity_id=integration.id,
        detail=f"Manual refresh simulated for {integration.name}. {REFRESH_MESSAGE}",
    )
    log.info("Simulated refresh for %s. No external system was contacted.", integration.key)
    return job

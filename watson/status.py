"""Commitment status.

A due date in the past never becomes Completed. Completion is only stored when
someone confirms it.
"""

from datetime import date

from watson.constants import COMMITMENT_STATUSES, TERMINAL_COMMITMENT_STATUSES


def effective_status(stored: str, due: date, today: date | None = None) -> str:
    today = today or date.today()
    if stored in TERMINAL_COMMITMENT_STATUSES:
        return stored
    if stored == "New" and due > today:
        return "New"
    if due < today:
        return "Overdue"
    if due == today:
        return "Due today"
    return "Upcoming"


def normalise_stored_status(requested: str, due: date, today: date | None = None) -> str:
    """Persist a status without letting the calendar mark work complete.

    Due today and Overdue are derived on read from the due date. New, Upcoming,
    Completed, Cancelled, and Unclear are stored as chosen.
    """

    today = today or date.today()
    if requested not in COMMITMENT_STATUSES:
        raise ValueError(f"Unknown commitment status: {requested}")
    if requested in TERMINAL_COMMITMENT_STATUSES or requested == "New":
        return requested
    if due < today or due == today or due > today:
        return "Upcoming"
    return "Upcoming"

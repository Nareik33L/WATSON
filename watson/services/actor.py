"""Phase 1 records actions as the demo user. There is no sign-in yet."""

from sqlalchemy.orm import Session

from watson.db.models import User
from watson.tenant import DEMO_USER_ID


def current_actor(session: Session) -> tuple[str | None, str]:
    user = session.get(User, DEMO_USER_ID)
    if user is None:
        return None, "Local user"
    return user.id, user.name

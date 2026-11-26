"""Phase 1 workspace.

There is one demo organisation. Later phases can resolve the organisation from
the signed-in user without reshaping the tables, which all carry organisation_id.
"""

DEMO_ORG_ID = "org_helios"
DEMO_USER_ID = "usr_alex"


def current_organisation_id() -> str:
    return DEMO_ORG_ID

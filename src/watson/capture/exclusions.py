"""Defence-in-depth capture exclusions (plan §3.2 / §5.1).

The helper is supposed to drop these frames before they reach the spool.
The Python ingest path applies the same rules so a failed filter cannot
write excluded content into ``frames`` or any content table.
"""

from __future__ import annotations

from typing import Any

from watson.config.schema import ExclusionsConfig

REASON_EXCLUDED_APP = "excluded_app"
REASON_EXCLUDED_TITLE = "excluded_title"
REASON_PRIVATE = "private_browsing"


def exclusion_reason(
    sidecar: dict[str, Any],
    exclusions: ExclusionsConfig,
) -> str | None:
    """Return a skip reason, or ``None`` if the frame may be stored."""
    bundle = str(sidecar.get("bundle_id") or "")
    app = str(sidecar.get("app_name") or "")
    title = str(sidecar.get("window_title") or "")
    excluded_apps = {item.casefold() for item in exclusions.bundle_ids if item}
    if bundle.casefold() in excluded_apps or app.casefold() in excluded_apps:
        return REASON_EXCLUDED_APP
    if sidecar.get("is_private_browsing") is True:
        return REASON_PRIVATE
    if sidecar.get("excluded") is True:
        return REASON_EXCLUDED_APP
    for pattern in exclusions.window_title_patterns:
        if pattern and pattern.casefold() in title.casefold():
            return REASON_EXCLUDED_TITLE
    return None

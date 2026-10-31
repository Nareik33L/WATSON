"""Typed WATSON configuration (pydantic v2). No secrets live here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Starter lists match helper/Sources/watson-capture/Filters.swift (plan §5.1).
DEFAULT_EXCLUDED_BUNDLE_IDS: tuple[str, ...] = (
    "com.1password.1password",
    "com.1password.1password-mac",
    "com.agilebits.onepassword7",
    "com.apple.FaceTime",
    "com.apple.Health",
    "com.apple.MobileSMS",
    "com.apple.Passwords",
    "com.apple.Photos",
    "com.apple.keychainaccess",
    "com.bitwarden.desktop",
    "com.dashlane.dashlanephonegeneration",
    "com.lastpass.LastPass",
)

DEFAULT_WINDOW_TITLE_PATTERNS: tuple[str, ...] = (
    "Private Browsing",
    "Incognito",
    "InPrivate",
    "1Password",
    "Sign in",
    "Sign-in",
    "Sign In",
    "Verification code",
    "Verification Code",
    "password",
)

DEFAULT_DENIED_DOMAINS: tuple[str, ...] = (
    "mail.google.com",
    "outlook.live.com",
    "icloud.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "chase.com",
    "bankofamerica.com",
    "wellsfargo.com",
    "paypal.com",
)

APPLICATION_SUPPORT_WATSON = Path("Library") / "Application Support" / "WATSON"

# TTLs from plan §5. Tier 2 is "consolidation + 7 days"; M3 has not landed, so
# the conservative M2c window is captured_at + tier2_grace_days (default 7).
DEFAULT_WINDOW_TITLE_DAYS = 30
DEFAULT_METADATA_DAYS = 365
DEFAULT_TIER2_GRACE_DAYS = 7
DEFAULT_SPOOL_HARD_LIMIT_HOURS = 24
DEFAULT_BACKLOG_BATCH_SIZE = 32

# D3 / §3.4: split when OCR-text similarity drops below this (word Jaccard).
DEFAULT_SEGMENT_SIMILARITY = 0.25
# §3.5 eligibility: a segment's state becomes closed after this dwell.
DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS = 20 * 60
# Cap on segments.merged_text (line-level union of redacted OCR).
DEFAULT_MERGED_TEXT_MAX_CHARS = 32768


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CaptureConfig(_StrictModel):
    """Adaptive capture interval bounds. Default 45 s; see plan §3.1 / §3.6."""

    interval_seconds: int = Field(default=45, ge=5, le=600)
    interval_min_seconds: int = Field(default=30, ge=5, le=600)
    interval_max_seconds: int = Field(default=120, ge=5, le=3600)
    dedup_threshold: int = Field(
        default=4,
        ge=0,
        le=64,
        description="Hamming distance at or below which consecutive frames are near-duplicates.",
    )


def _clean_ids(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("exclusion lists must be arrays of strings")
    return tuple(item.strip() for item in (str(raw) for raw in value) if item.strip())


def _extend_defaults(
    defaults: tuple[str, ...],
    extras: tuple[str, ...],
    *,
    casefold: bool = True,
) -> tuple[str, ...]:
    """Keep built-in defaults intact and append user extras that are not already present."""
    seen = {item.casefold() if casefold else item for item in defaults}
    out = list(defaults)
    for item in extras:
        key = item.casefold() if casefold else item
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return tuple(out)


class ExclusionsConfig(_StrictModel):
    """Capture exclusions (plan §5.1). Prevention only; not a secret store.

    User lists **merge** with the built-in defaults. Password managers and
    other defaults stay excluded unless named in ``allowed_bundle_ids``.
    """

    bundle_ids: tuple[str, ...] = DEFAULT_EXCLUDED_BUNDLE_IDS
    window_title_patterns: tuple[str, ...] = DEFAULT_WINDOW_TITLE_PATTERNS
    denied_domains: tuple[str, ...] = DEFAULT_DENIED_DOMAINS
    allowed_domains: tuple[str, ...] = ()
    allowed_bundle_ids: tuple[str, ...] = Field(
        default=(),
        description="Visible override: these bundle IDs are not excluded.",
    )

    @model_validator(mode="before")
    @classmethod
    def merge_builtin_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        incoming = dict(data)
        allowed = {item.casefold() for item in _clean_ids(incoming.get("allowed_bundle_ids"))}
        extras = _clean_ids(incoming["bundle_ids"]) if "bundle_ids" in incoming else ()
        incoming["bundle_ids"] = tuple(
            item
            for item in _extend_defaults(DEFAULT_EXCLUDED_BUNDLE_IDS, extras)
            if item.casefold() not in allowed
        )
        if "window_title_patterns" in incoming:
            incoming["window_title_patterns"] = _extend_defaults(
                DEFAULT_WINDOW_TITLE_PATTERNS,
                _clean_ids(incoming.get("window_title_patterns")),
                casefold=False,
            )
        if "denied_domains" in incoming:
            incoming["denied_domains"] = _extend_defaults(
                DEFAULT_DENIED_DOMAINS,
                _clean_ids(incoming.get("denied_domains")),
            )
        if "allowed_bundle_ids" in incoming:
            incoming["allowed_bundle_ids"] = _clean_ids(incoming.get("allowed_bundle_ids"))
        if "allowed_domains" in incoming:
            incoming["allowed_domains"] = _clean_ids(incoming.get("allowed_domains"))
        return incoming

    def overridden_default_bundle_ids(self) -> tuple[str, ...]:
        """Built-in defaults that ``allowed_bundle_ids`` has un-excluded."""
        allowed = {item.casefold() for item in self.allowed_bundle_ids}
        return tuple(item for item in DEFAULT_EXCLUDED_BUNDLE_IDS if item.casefold() in allowed)


class SegmentationConfig(_StrictModel):
    """Incremental activity segments (plan D3 / §3.4)."""

    similarity_threshold: float = Field(
        default=DEFAULT_SEGMENT_SIMILARITY,
        ge=0.0,
        le=1.0,
        description="Minimum OCR-text word Jaccard to keep consecutive frames in one segment.",
    )
    close_after_seconds: int = Field(
        default=DEFAULT_SEGMENT_CLOSE_AFTER_SECONDS,
        ge=60,
        le=86_400,
        description="Mark a segment closed (eligible) once dwell reaches this many seconds.",
    )
    merged_text_max_chars: int = Field(
        default=DEFAULT_MERGED_TEXT_MAX_CHARS,
        ge=256,
        le=200_000,
        description="Maximum characters kept in the order-preserving line union.",
    )


class RetentionConfig(_StrictModel):
    """Retention windows (plan §5). Tier-2 and spool sweeps honor these."""

    window_title_days: int = Field(default=DEFAULT_WINDOW_TITLE_DAYS, ge=0, le=3650)
    metadata_days: int = Field(default=DEFAULT_METADATA_DAYS, ge=0, le=3650)
    tier2_grace_days: int = Field(
        default=DEFAULT_TIER2_GRACE_DAYS,
        ge=0,
        le=30,
        description="Age window for frame_text / frame_visual / segments.merged_text.",
    )
    spool_hard_limit_hours: int = Field(
        default=DEFAULT_SPOOL_HARD_LIMIT_HOURS,
        ge=1,
        le=168,
        description="Tier-0 hard limit: stale spool PNGs become deleted_unprocessed.",
    )
    derived_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        description="Optional rolling window for claims/summaries; None = indefinite.",
    )


class PipelineConfig(_StrictModel):
    """Worker drain bounds. Capture/ingest never waits on this."""

    backlog_batch_size: int = Field(
        default=DEFAULT_BACKLOG_BATCH_SIZE,
        ge=1,
        le=10_000,
        description="Max extract jobs completed in one drain_backlog call.",
    )


class WatsonConfig(_StrictModel):
    """Resolved runtime configuration. Paths are absolute after load()."""

    data_dir: Path
    config_path: Path
    db_path: Path
    spool_dir: Path
    logs_dir: Path
    models_dir: Path
    session_dir: Path
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    exclusions: ExclusionsConfig = Field(default_factory=ExclusionsConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    segmentation: SegmentationConfig = Field(default_factory=SegmentationConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)

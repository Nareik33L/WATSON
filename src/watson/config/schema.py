"""Typed WATSON configuration (pydantic v2). No secrets live here."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

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

# Placeholder TTLs from plan §5. Enforcement lands with M2/M3 retention.
DEFAULT_WINDOW_TITLE_DAYS = 30
DEFAULT_METADATA_DAYS = 365
DEFAULT_TIER2_GRACE_DAYS = 7


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CaptureConfig(_StrictModel):
    """Adaptive capture interval bounds. Default 45 s; see plan §3.1 / §3.6."""

    interval_seconds: int = Field(default=45, ge=5, le=600)
    interval_min_seconds: int = Field(default=30, ge=5, le=600)
    interval_max_seconds: int = Field(default=120, ge=5, le=3600)


class ExclusionsConfig(_StrictModel):
    """Capture exclusions (plan §5.1). Prevention only; not a secret store."""

    bundle_ids: tuple[str, ...] = DEFAULT_EXCLUDED_BUNDLE_IDS
    window_title_patterns: tuple[str, ...] = DEFAULT_WINDOW_TITLE_PATTERNS
    denied_domains: tuple[str, ...] = DEFAULT_DENIED_DOMAINS
    allowed_domains: tuple[str, ...] = ()


class RetentionConfig(_StrictModel):
    """Retention placeholders (plan §5). Not enforced in this slice."""

    window_title_days: int = Field(default=DEFAULT_WINDOW_TITLE_DAYS, ge=0, le=3650)
    metadata_days: int = Field(default=DEFAULT_METADATA_DAYS, ge=0, le=3650)
    tier2_grace_days: int = Field(default=DEFAULT_TIER2_GRACE_DAYS, ge=0, le=365)
    derived_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        description="Optional rolling window for claims/summaries; None = indefinite.",
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

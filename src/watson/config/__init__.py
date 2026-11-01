"""Settings schema and defaults."""

from watson.config.load import (
    ConfigError,
    default_data_dir,
    ensure_runtime_dirs,
    is_cloud_synced_path,
    load_config,
)
from watson.config.schema import (
    APPLICATION_SUPPORT_WATSON,
    DEFAULT_DENIED_DOMAINS,
    DEFAULT_EXCLUDED_BUNDLE_IDS,
    DEFAULT_WINDOW_TITLE_PATTERNS,
    CaptureConfig,
    ExclusionsConfig,
    IntelligenceConfig,
    RetentionConfig,
    WatsonConfig,
)

__all__ = [
    "APPLICATION_SUPPORT_WATSON",
    "DEFAULT_DENIED_DOMAINS",
    "DEFAULT_EXCLUDED_BUNDLE_IDS",
    "DEFAULT_WINDOW_TITLE_PATTERNS",
    "CaptureConfig",
    "ConfigError",
    "ExclusionsConfig",
    "IntelligenceConfig",
    "RetentionConfig",
    "WatsonConfig",
    "default_data_dir",
    "ensure_runtime_dirs",
    "is_cloud_synced_path",
    "load_config",
]

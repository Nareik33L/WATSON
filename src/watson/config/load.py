"""Load WatsonConfig from the §2.1 TOML path with environment overrides."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from watson.config.schema import APPLICATION_SUPPORT_WATSON, WatsonConfig

log = logging.getLogger("watson.config")

_PATH_ENV = {
    "WATSON_DATA_DIR": "data_dir",
    "WATSON_CONFIG_PATH": "config_path",
    "WATSON_DB_PATH": "db_path",
    "WATSON_SPOOL_DIR": "spool_dir",
    "WATSON_LOGS_DIR": "logs_dir",
    "WATSON_MODELS_DIR": "models_dir",
    "WATSON_SESSION_DIR": "session_dir",
}


class ConfigError(ValueError):
    """Invalid configuration. ``str(error)`` names the bad key."""


def default_data_dir() -> Path:
    """§2.1 runtime root: ~/Library/Application Support/WATSON."""
    return Path.home() / APPLICATION_SUPPORT_WATSON


def format_validation_error(exc: ValidationError) -> str:
    """Turn a pydantic error into ``key: message`` clauses (dotted paths)."""
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(item) for item in err["loc"]) or "<root>"
        parts.append(f"{loc}: {err['msg']}")
    return "Invalid config: " + "; ".join(parts)


def load_config(
    *,
    config_path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> WatsonConfig:
    """Load defaults, optional TOML, then ``WATSON_*`` overrides.

    Validation failures name the offending key (e.g. ``capture.interval_seconds``).
    """
    env: Mapping[str, str] = os.environ if environ is None else environ
    data_dir_guess = _env_path(env, "WATSON_DATA_DIR") or default_data_dir()
    resolved_config_path = (
        config_path
        if config_path is not None
        else _env_path(env, "WATSON_CONFIG_PATH") or (data_dir_guess / "config.toml")
    )

    file_data: dict[str, Any] = {}
    if resolved_config_path.is_file():
        try:
            loaded = tomllib.loads(resolved_config_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(
                f"Invalid config: {resolved_config_path}: TOML parse error: {exc}"
            ) from exc
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ConfigError(f"Invalid config: {resolved_config_path}: root must be a table")
        file_data = loaded

    data_dir = _env_path(env, "WATSON_DATA_DIR") or _as_path(
        file_data.get("data_dir"), data_dir_guess
    )
    merged = _defaults_for(data_dir, resolved_config_path)
    _deep_update(merged, file_data)
    _apply_env_overrides(merged, env)
    _recompute_derived_paths(merged, file_data, env)
    merged["config_path"] = str(resolved_config_path.expanduser())
    merged["data_dir"] = str(Path(str(merged["data_dir"])).expanduser())

    try:
        config = WatsonConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(format_validation_error(exc)) from exc
    _warn_exclusion_overrides(config)
    return config


def _warn_exclusion_overrides(config: WatsonConfig) -> None:
    for bundle_id in config.exclusions.overridden_default_bundle_ids():
        log.warning(
            "allowed_bundle_ids un-excludes built-in default %s; "
            "password managers and other defaults stay excluded unless this is intentional",
            bundle_id,
        )


def _defaults_for(data_dir: Path, config_path: Path) -> dict[str, Any]:
    root = data_dir.expanduser()
    return {
        "data_dir": str(root),
        "config_path": str(config_path.expanduser()),
        "db_path": str(root / "watson.db"),
        "spool_dir": str(root / "spool"),
        "logs_dir": str(root / "logs"),
        "models_dir": str(root / "models"),
        "session_dir": str(root / "session"),
        "capture": {},
        "exclusions": {},
        "retention": {},
    }


def _apply_env_overrides(merged: dict[str, Any], env: Mapping[str, str]) -> None:
    for env_name, key in _PATH_ENV.items():
        value = _env_path(env, env_name)
        if value is not None:
            merged[key] = str(value)
    interval = env.get("WATSON_CAPTURE_INTERVAL")
    if interval is not None and interval != "":
        capture = dict(cast(dict[str, Any], merged.get("capture") or {}))
        capture["interval_seconds"] = interval
        merged["capture"] = capture


def _recompute_derived_paths(
    merged: dict[str, Any],
    file_data: dict[str, Any],
    env: Mapping[str, str],
) -> None:
    """If data_dir changed but a derived path was not set, follow data_dir."""
    root = Path(str(merged["data_dir"])).expanduser()
    implicit = {
        "db_path": root / "watson.db",
        "spool_dir": root / "spool",
        "logs_dir": root / "logs",
        "models_dir": root / "models",
        "session_dir": root / "session",
    }
    for key, default in implicit.items():
        env_name = next(name for name, field in _PATH_ENV.items() if field == key)
        if env_name in env and env[env_name] != "":
            continue
        if key in file_data:
            continue
        merged[key] = str(default)


def _deep_update(base: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            child = dict(cast(dict[str, Any], base[key]))
            _deep_update(child, value)
            base[key] = child
        else:
            base[key] = value


def _env_path(env: Mapping[str, str], name: str) -> Path | None:
    raw = env.get(name)
    if raw is None or raw == "":
        return None
    return Path(raw).expanduser()


def _as_path(value: object, fallback: Path) -> Path:
    if value is None or value == "":
        return fallback
    return Path(str(value)).expanduser()


_CLOUD_MARKERS = (
    "Mobile Documents",
    "Dropbox",
    "Google Drive",
    "OneDrive",
    "iCloud",
)


def is_cloud_synced_path(path: Path) -> bool:
    text = str(path)
    return any(marker in text for marker in _CLOUD_MARKERS)


def ensure_runtime_dirs(config: WatsonConfig) -> None:
    """Create §2.1 directories with 0700. Spool is Time Machine-excluded on macOS."""
    for directory in (
        config.data_dir,
        config.spool_dir,
        config.logs_dir,
        config.models_dir,
        config.session_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
    _exclude_from_time_machine(config.spool_dir)


def _exclude_from_time_machine(path: Path) -> None:
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            [
                "xattr",
                "-w",
                "com.apple.metadata:com_apple_backup_excludeItem",
                "com.apple.backupd",
                str(path),
            ],
            check=False,
            capture_output=True,
        )
    except OSError:
        return

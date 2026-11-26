"""Configuration from config/default.yaml, optional config/local.yaml, then environment, then CLI."""

from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass
class Settings:
    host: str
    port: int
    open_browser: bool
    database_path: Path
    log_level: str
    root: Path
    frontend_dist: Path
    config_files: list[str]


def load_settings(args) -> Settings:
    default_path = ROOT / "config" / "default.yaml"
    if not default_path.is_file():
        raise SystemExit(f"Missing configuration file: {default_path}")
    data = _read_yaml(default_path)
    files = [str(default_path)]
    local_path = ROOT / "config" / "local.yaml"
    if local_path.is_file():
        data.update(_read_yaml(local_path))
        files.append(str(local_path))

    import os

    if os.environ.get("WATSON_HOST"):
        data["host"] = os.environ["WATSON_HOST"]
    if os.environ.get("WATSON_PORT"):
        data["port"] = os.environ["WATSON_PORT"]
    if os.environ.get("WATSON_DATABASE"):
        data["database_path"] = os.environ["WATSON_DATABASE"]
    if os.environ.get("WATSON_LOG_LEVEL"):
        data["log_level"] = os.environ["WATSON_LOG_LEVEL"]
    if os.environ.get("WATSON_NO_BROWSER") == "1":
        data["open_browser"] = False

    if getattr(args, "host", None):
        data["host"] = args.host
    if getattr(args, "port", None):
        data["port"] = args.port
    if getattr(args, "database", None):
        data["database_path"] = args.database
    if getattr(args, "log_level", None):
        data["log_level"] = args.log_level
    if getattr(args, "no_browser", False):
        data["open_browser"] = False

    host = str(data.get("host", "127.0.0.1")).strip()
    port = _port(data.get("port", 8741))
    database = Path(str(data.get("database_path", "data/command.db")))
    if not database.is_absolute():
        database = ROOT / database
    log_level = str(data.get("log_level", "INFO")).upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise SystemExit(f"Unknown log_level {log_level}. Use DEBUG, INFO, WARNING, or ERROR.")
    return Settings(
        host=host,
        port=port,
        open_browser=bool(data.get("open_browser", True)),
        database_path=database,
        log_level=log_level,
        root=ROOT,
        frontend_dist=ROOT / "frontend" / "dist",
        config_files=files,
    )


def assert_loopback(host: str) -> None:
    if host.strip().lower() not in LOOPBACK_HOSTS:
        raise SystemExit(f"Refusing to bind to {host}. This prototype listens on localhost only.")


def _read_yaml(path: Path) -> dict:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SystemExit(f"Could not read {path}: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise SystemExit(f"{path} must contain a mapping of settings.")
    return loaded


def _port(value) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit("Port must be a number.") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("Port must be between 1 and 65535.")
    return port

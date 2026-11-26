"""Start, serve, and stop the local command centre."""

import argparse
import logging
import logging.config
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from watson.api.app import create_app
from watson.config import Settings, assert_loopback, load_settings

log = logging.getLogger("watson.startup")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        settings = load_settings(args)
        configure_logging(settings.log_level)
        _log_configuration(settings)
        assert_loopback(settings.host)
        ensure_frontend(settings.root)
        serve(settings)
    except KeyboardInterrupt:
        log.info("Interrupted")
    except SystemExit:
        raise
    except Exception:
        logging.getLogger("watson.startup").exception("Startup failed")
        raise SystemExit(1) from None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the Sales Command Centre on localhost.")
    parser.add_argument("--host", default=None, help="Loopback host. Defaults to 127.0.0.1.")
    parser.add_argument("--port", type=int, default=None, help="Port. Defaults to 8741.")
    parser.add_argument("--database", default=None, help="SQLite file. Defaults to data/command.db.")
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, or ERROR.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser window.")
    return parser.parse_args(argv)


def configure_logging(level: str) -> None:
    logging.config.dictConfig(_logging_dict(level))


def ensure_frontend(root: Path) -> None:
    frontend = root / "frontend"
    dist_index = frontend / "dist" / "index.html"
    npm = shutil.which("npm")
    modules = frontend / "node_modules"
    package = frontend / "package.json"
    needs_install = (not modules.exists()) or (package.exists() and package.stat().st_mtime > modules.stat().st_mtime)
    needs_build = _newer_than(dist_index, frontend)
    if not needs_install and not needs_build:
        log.info("Dashboard assets are up to date")
        return
    if npm is None:
        if dist_index.is_file() and not needs_build:
            log.warning("npm was not found. Serving the existing dashboard build.")
            return
        log.error("Node.js and npm are required to build the dashboard. Install Node.js 20 or newer, then run python3 main.py again.")
        raise SystemExit(1)
    if needs_install:
        log.info("Installing dashboard dependencies")
        _run([npm, "install", "--no-fund", "--no-audit"], frontend)
    if needs_build or needs_install:
        log.info("Building the dashboard")
        _run([npm, "run", "build"], frontend)
    if not dist_index.is_file():
        log.error("The dashboard build did not produce frontend/dist/index.html")
        raise SystemExit(1)
    log.info("Dashboard build ready")


def serve(settings: Settings) -> None:
    assert_loopback(settings.host)
    if not _port_available(settings.host, settings.port):
        log.error(
            "Port %s on %s is already in use. Stop the other process or run python3 main.py --port %s",
            settings.port,
            settings.host,
            settings.port + 1,
        )
        raise SystemExit(1)
    import uvicorn

    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_config=_logging_dict(settings.log_level),
        access_log=True,
    )
    server = uvicorn.Server(config)
    url = f"http://{settings.host}:{settings.port}/"
    log.info("External AI APIs: disabled")
    log.info("Live integrations: disabled")
    log.info("Dashboard ready at %s", url)
    log.info("Press Ctrl+C to stop the server")
    if settings.open_browser:
        threading.Thread(target=_open_browser, args=(url,), name="open-browser", daemon=True).start()
    try:
        server.run()
    finally:
        log.info("Sales Command Centre stopped")


def _open_browser(url: str) -> None:
    health = url.rstrip("/") + "/api/health"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health, timeout=1) as response:
                if response.status == 200:
                    break
        except Exception:
            time.sleep(0.25)
    else:
        log.warning("The server did not answer a health check, so the browser was not opened. Open %s", url)
        return
    try:
        opened = webbrowser.open(url)
    except Exception:
        log.warning("Could not open a browser automatically. Open %s", url)
        return
    if opened:
        log.info("Opened the dashboard in the default browser")
    else:
        log.info("No browser was opened automatically. Open %s", url)


def _port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _run(command: list[str], cwd: Path) -> None:
    log.info("Running %s", " ".join(command))
    completed = subprocess.run(command, cwd=cwd)
    if completed.returncode != 0:
        log.error("Command failed (%s): %s", completed.returncode, " ".join(command))
        raise SystemExit(1)


def _newer_than(output: Path, folder: Path) -> bool:
    if not output.is_file():
        return True
    stamp = output.stat().st_mtime
    for path in folder.rglob("*"):
        if "node_modules" in path.parts or "dist" in path.parts:
            continue
        if path.is_file() and path.stat().st_mtime > stamp:
            return True
    return False


def _log_configuration(settings: Settings) -> None:
    log.info("Checking dependencies")
    log.info("Python %s", sys.version.split()[0])
    log.info("Configuration: %s", ", ".join(settings.config_files))
    log.info("Host %s port %s", settings.host, settings.port)
    log.info("Database %s", settings.database_path)
    log.info("Demo data: enabled")


def _logging_dict(level: str) -> dict:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "std": {
                "format": "%(asctime)s %(levelname)-5s %(name)s  %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "std",
                "stream": "ext://sys.stderr",
            }
        },
        "loggers": {
            "uvicorn": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": ["console"], "level": level, "propagate": False},
            "watson": {"handlers": ["console"], "level": level, "propagate": False},
        },
        "root": {"handlers": ["console"], "level": level},
    }

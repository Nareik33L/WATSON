"""Start the loopback-only dashboard (``watson dashboard``)."""

from __future__ import annotations

import logging

from watson.config import ensure_runtime_dirs, load_config
from watson.dashboard.app import create_app
from watson.dashboard.bind import DEFAULT_PORT, bind_available_port, validate_bind_host
from watson.dashboard.tokens import get_token_provider

log = logging.getLogger("watson.dashboard")


def run_dashboard(
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    open_browser: bool = False,
) -> int:
    bound_host = validate_bind_host(host)
    config = load_config()
    ensure_runtime_dirs(config)
    token = get_token_provider(config.session_dir).get_token()
    sock = bind_available_port(bound_host, port)
    chosen = int(sock.getsockname()[1])
    try:
        app = create_app(config=config, token=token, port=chosen)
        code = app.state.launch_codes.mint()
        public = f"http://{bound_host}:{chosen}/"
        auth = f"{public}auth?code={code}"
        print(f"dashboard: {public}")
        print(f"auth: {auth}")
        log.info("dashboard listening", extra={"host": bound_host, "port": chosen})
        if open_browser:
            import webbrowser

            webbrowser.open(auth)
        import uvicorn

        uvicorn.run(app, fd=sock.fileno(), log_level="warning")
    finally:
        sock.close()
    return 0

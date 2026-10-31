"""FastAPI status page: server-rendered Jinja + vendored HTMX, loopback auth."""

from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlcipher3 import dbapi2 as sqlcipher
from starlette.middleware.base import BaseHTTPMiddleware

from watson.config.schema import WatsonConfig
from watson.dashboard.auth import (
    CSRF_FORM_FIELD,
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    AuthError,
    LaunchCodes,
    cookie_kwargs,
    csrf_matches,
    host_allowed,
    new_session,
    origin_allowed,
    session_from_request,
    sign_session,
    tokens_equal,
)
from watson.dashboard.bind import DEFAULT_PORT
from watson.memory.db import DatabaseError, connect
from watson.memory.keys import KeyProviderError, get_key_provider
from watson.pipeline.status import StatusReport, collect_status
from watson.pipeline.today import TodayReport, collect_today

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"

CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'"
)

_TEMPLATES = Jinja2Templates(directory=str(TEMPLATES_DIR))


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        app = request.app
        expected_port: int | None = getattr(app.state, "port", None)
        if not host_allowed(request.headers.get("host"), expected_port=expected_port):
            return _secure(HTMLResponse("invalid host", status_code=400))
        origin = request.headers.get("origin")
        if not origin_allowed(origin, expected_port=expected_port):
            return _secure(HTMLResponse("invalid origin", status_code=400))

        path = request.url.path
        if path.startswith("/static/") or path == "/auth":
            return _secure(await call_next(request))

        secret: str = app.state.token
        session = session_from_request(request, secret)
        if session is None:
            return _secure(_unauthenticated(request))

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if origin is None or origin == "":
                return _secure(HTMLResponse("missing origin", status_code=400))
            form_csrf = await _form_csrf(request)
            if not csrf_matches(request, session) and not (
                form_csrf is not None and tokens_equal(form_csrf, session.csrf)
            ):
                return _secure(HTMLResponse("csrf", status_code=403))

        request.state.session = session
        return _secure(await call_next(request))


def create_app(
    *,
    config: WatsonConfig | None = None,
    token: str,
    port: int = DEFAULT_PORT,
    session_epoch: int | None = None,
    session_ttl: int = SESSION_TTL_SECONDS,
    clock: Callable[[], float] | None = None,
    launch_codes: LaunchCodes | None = None,
) -> FastAPI:
    """Build the dashboard app.

    ``token`` is the per-install secret: it signs session cookies and never
    appears in a URL or log. ``watson dashboard`` mints a single-use launch
    code that ``/auth?code=`` exchanges for a session.
    """
    if not token:
        raise AuthError("dashboard token must not be empty")

    app = FastAPI(title="WATSON", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token
    app.state.config = config
    app.state.port = port
    app.state.session_epoch = secrets.randbits(64) if session_epoch is None else session_epoch
    app.state.session_ttl = session_ttl
    app.state.clock = clock if clock is not None else time.time
    app.state.launch_codes = launch_codes if launch_codes is not None else LaunchCodes()

    app.add_middleware(SecurityHeadersMiddleware)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/auth")
    async def auth(request: Request, code: str | None = None, token: str | None = None) -> Response:
        if token is not None:
            return HTMLResponse("unauthorized", status_code=401)
        codes: LaunchCodes = request.app.state.launch_codes
        if code is None or not codes.consume(code):
            return HTMLResponse("unauthorized", status_code=401)
        secret: str = request.app.state.token
        session = new_session()
        ttl = int(request.app.state.session_ttl)
        flags = cookie_kwargs(max_age=ttl)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            sign_session(
                session,
                secret,
                epoch=int(request.app.state.session_epoch),
                issued_at=int(request.app.state.clock()),
            ),
            httponly=bool(flags["httponly"]),
            samesite="strict",
            secure=bool(flags["secure"]),
            path=str(flags["path"]),
            max_age=ttl,
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    async def status_page(request: Request) -> Response:
        return _render(request, "status.html")

    @app.get("/status/partial", response_class=HTMLResponse)
    async def status_partial(request: Request) -> Response:
        return _render(request, "status_partial.html")

    @app.post("/status/refresh", response_class=HTMLResponse)
    async def status_refresh(request: Request) -> Response:
        if _is_htmx(request):
            return _render(request, "status_partial.html")
        return RedirectResponse("/", status_code=303)

    @app.get("/today", response_class=HTMLResponse)
    async def today_page(request: Request) -> Response:
        return _render(request, "today.html")

    @app.get("/today/partial", response_class=HTMLResponse)
    async def today_partial(request: Request) -> Response:
        return _render(request, "today_partial.html")

    @app.get("/api/status")
    async def api_status(request: Request) -> JSONResponse:
        return JSONResponse(_report(request).as_dict())

    @app.get("/api/today")
    async def api_today(request: Request) -> JSONResponse:
        return JSONResponse(_today(request).as_dict())

    return app


def _render(request: Request, template_name: str) -> HTMLResponse:
    session = request.state.session
    report = _report(request)
    today = _today(request) if "today" in template_name else None
    return _TEMPLATES.TemplateResponse(
        request,
        template_name,
        {
            "status": report,
            "today": today,
            "csrf_token": session.csrf,
        },
    )


def _cached_db_key(app: FastAPI) -> bytes:
    key = getattr(app.state, "db_key", None)
    if isinstance(key, bytes):
        return key
    fetched = get_key_provider().get_key()
    app.state.db_key = fetched
    return fetched


def _report(request: Request) -> StatusReport:
    config = getattr(request.app.state, "config", None)
    overrides = config.exclusions.overridden_default_bundle_ids() if config is not None else ()
    if config is None or not config.db_path.is_file():
        return StatusReport({}, {}, None, exclusion_overrides=overrides)
    try:
        key = _cached_db_key(request.app)
        conn = connect(config.db_path, key, load_vec=False, check_same_thread=False)
    except (KeyProviderError, DatabaseError, sqlcipher.DatabaseError):
        return StatusReport({}, {}, None, exclusion_overrides=overrides)
    try:
        return collect_status(conn, exclusions=config.exclusions)
    except (DatabaseError, sqlcipher.DatabaseError):
        return StatusReport({}, {}, None, exclusion_overrides=overrides)
    finally:
        conn.close()


def _today(request: Request) -> TodayReport:
    config = getattr(request.app.state, "config", None)
    if config is None or not config.db_path.is_file():
        return TodayReport(date=None, segments=())
    try:
        key = _cached_db_key(request.app)
        conn = connect(config.db_path, key, load_vec=False, check_same_thread=False)
    except (KeyProviderError, DatabaseError, sqlcipher.DatabaseError):
        return TodayReport(date=None, segments=())
    try:
        return collect_today(conn)
    except (DatabaseError, sqlcipher.DatabaseError):
        return TodayReport(date=None, segments=())
    finally:
        conn.close()


def _unauthenticated(request: Request) -> Response:
    if _is_htmx(request) or request.url.path.startswith("/api/"):
        return HTMLResponse("unauthorized", status_code=401)
    if request.method == "GET":
        return RedirectResponse("/auth", status_code=303)
    return HTMLResponse("unauthorized", status_code=401)


def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request", "").lower() == "true"


async def _form_csrf(request: Request) -> str | None:
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("application/x-www-form-urlencoded"):
        return None
    form = await request.form()
    value = form.get(CSRF_FORM_FIELD)
    return value if isinstance(value, str) else None


def _secure(response: Response) -> Response:
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response

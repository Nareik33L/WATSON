"""FastAPI status page: server-rendered Jinja + vendored HTMX, loopback auth."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from watson.config.schema import WatsonConfig
from watson.dashboard.auth import (
    CSRF_FORM_FIELD,
    SESSION_COOKIE,
    AuthError,
    cookie_kwargs,
    csrf_matches,
    host_allowed,
    new_session,
    origin_allowed,
    session_from_request,
    sign_session,
    tokens_equal,
)
from watson.memory.db import DatabaseError, connect
from watson.memory.keys import KeyProviderError, get_key_provider
from watson.pipeline.status import StatusReport, collect_status

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
    port: int | None = None,
) -> FastAPI:
    """Build the dashboard app. ``token`` is the per-install secret (never logged)."""
    if not token:
        raise AuthError("dashboard token must not be empty")

    app = FastAPI(title="WATSON", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token
    app.state.config = config
    app.state.port = port

    app.add_middleware(SecurityHeadersMiddleware)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/auth")
    async def auth(request: Request, token: str | None = None) -> Response:
        secret: str = request.app.state.token
        if token is None or not tokens_equal(token, secret):
            return HTMLResponse("unauthorized", status_code=401)
        session = new_session()
        response = RedirectResponse("/", status_code=303)
        flags = cookie_kwargs()
        response.set_cookie(
            SESSION_COOKIE,
            sign_session(session, secret),
            httponly=bool(flags["httponly"]),
            samesite="strict",
            secure=bool(flags["secure"]),
            path=str(flags["path"]),
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

    @app.get("/api/status")
    async def api_status(request: Request) -> JSONResponse:
        return JSONResponse(_report(request).as_dict())

    return app


def _render(request: Request, template_name: str) -> HTMLResponse:
    session = request.state.session
    report = _report(request)
    return _TEMPLATES.TemplateResponse(
        request,
        template_name,
        {
            "status": report,
            "csrf_token": session.csrf,
        },
    )


def _report(request: Request) -> StatusReport:
    config = getattr(request.app.state, "config", None)
    if config is None or not config.db_path.is_file():
        return StatusReport({}, {}, None)
    try:
        key = get_key_provider().get_key()
        conn = connect(config.db_path, key, load_vec=False, check_same_thread=False)
    except (KeyProviderError, DatabaseError):
        return StatusReport({}, {}, None)
    try:
        return collect_status(conn)
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

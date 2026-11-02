"""FastAPI status page: server-rendered Jinja + vendored HTMX, loopback auth."""

from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
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
from watson.memory.corrections import (
    CorrectionError,
    merge_entities,
    record_claim_correction,
    rename_entity,
)
from watson.memory.db import DatabaseError, connect
from watson.memory.keys import KeyProviderError, get_key_provider
from watson.pipeline.day_page import DayPage, collect_day_page
from watson.pipeline.entities_page import EntityPage, EntityType, collect_entities
from watson.pipeline.frames import PIPELINE_VERSION
from watson.pipeline.history import HistoryPage, collect_history
from watson.pipeline.memory_page import MemoryPage, collect_memory, memory_claim
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
            if not csrf_matches(request, session):
                form_csrf = await _form_csrf(request)
                if form_csrf is None or not tokens_equal(form_csrf, session.csrf):
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

    @app.get("/days", response_class=HTMLResponse)
    async def days_page(request: Request, date: str | None = None) -> Response:
        return _render(request, "days.html", date=date)

    @app.post("/days/{working_day_id}/retry", response_class=HTMLResponse)
    async def days_retry(request: Request, working_day_id: str) -> Response:
        date = _retry_day(request, working_day_id)
        if _is_htmx(request):
            return _render(request, "days_partial.html", date=date)
        target = f"/days?date={date}" if date else "/days"
        return RedirectResponse(target, status_code=303)

    @app.get("/history", response_class=HTMLResponse)
    async def history_page(request: Request) -> Response:
        return _render(request, "history.html")

    @app.get("/projects", response_class=HTMLResponse)
    @app.get("/projects/{entity_id}", response_class=HTMLResponse)
    async def projects_page(request: Request, entity_id: str | None = None) -> Response:
        return _render(request, "entities.html", entity_type="project", entity_id=entity_id)

    @app.get("/people", response_class=HTMLResponse)
    @app.get("/people/{entity_id}", response_class=HTMLResponse)
    async def people_page(request: Request, entity_id: str | None = None) -> Response:
        return _render(request, "entities.html", entity_type="person", entity_id=entity_id)

    @app.get("/memory", response_class=HTMLResponse)
    async def memory_page(request: Request, date: str | None = None) -> Response:
        return _render(request, "memory.html", date=date)

    @app.get("/memory/partial", response_class=HTMLResponse)
    async def memory_partial(request: Request, date: str | None = None) -> Response:
        return _render(request, "memory_partial.html", date=date)

    @app.post("/claims/{claim_id}/feedback", response_class=HTMLResponse)
    async def claim_feedback(request: Request, claim_id: str) -> Response:
        try:
            date = await _claim_feedback(request, claim_id)
        except CorrectionError as exc:
            return HTMLResponse(str(exc), status_code=exc.status_code)
        if _is_htmx(request):
            return _render(request, "memory_partial.html", date=date)
        target = f"/memory?date={date}" if date else "/memory"
        return RedirectResponse(target, status_code=303)

    @app.post("/entities/{entity_id}/rename", response_class=HTMLResponse)
    async def entity_rename(request: Request, entity_id: str) -> Response:
        try:
            dest = await _entity_rename(request, entity_id)
        except CorrectionError as exc:
            return HTMLResponse(str(exc), status_code=exc.status_code)
        path = _entity_path(dest[0])
        if _is_htmx(request):
            return _render(request, "entities.html", entity_type=dest[0], entity_id=dest[1])
        return RedirectResponse(f"/{path}/{dest[1]}", status_code=303)

    @app.post("/entities/{entity_id}/merge", response_class=HTMLResponse)
    async def entity_merge(request: Request, entity_id: str) -> Response:
        try:
            dest = await _entity_merge(request, entity_id)
        except CorrectionError as exc:
            return HTMLResponse(str(exc), status_code=exc.status_code)
        path = _entity_path(dest[0])
        if _is_htmx(request):
            return _render(request, "entities.html", entity_type=dest[0], entity_id=dest[1])
        return RedirectResponse(f"/{path}/{dest[1]}", status_code=303)

    @app.get("/api/status")
    async def api_status(request: Request) -> JSONResponse:
        return JSONResponse(_report(request).as_dict())

    @app.get("/api/today")
    async def api_today(request: Request) -> JSONResponse:
        return JSONResponse(_today(request).as_dict())

    @app.get("/api/history")
    async def api_history(request: Request) -> JSONResponse:
        return JSONResponse(_history(request).as_dict())

    @app.get("/api/entities")
    async def api_entities(
        request: Request,
        entity_type: str = Query("project", alias="type"),
        entity_id: str | None = Query(None, alias="id"),
    ) -> JSONResponse:
        if entity_type not in {"project", "person"}:
            return JSONResponse({"error": "type must be project or person"}, status_code=400)
        return JSONResponse(_entities(request, entity_type, entity_id).as_dict())

    @app.get("/api/claims")
    async def api_claims(
        request: Request,
        date: str | None = None,
        claim_id: str | None = Query(None, alias="id"),
    ) -> JSONResponse:
        if claim_id:
            item = _one_claim(request, claim_id)
            if item is None:
                return JSONResponse({"error": "claim not found"}, status_code=404)
            return JSONResponse(item)
        return JSONResponse(_memory(request, date).as_dict())

    @app.post("/api/claims/{claim_id}/feedback")
    async def api_claim_feedback(request: Request, claim_id: str) -> JSONResponse:
        try:
            date = await _claim_feedback(request, claim_id, json_body=True)
        except CorrectionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status_code)
        return JSONResponse({"ok": True, "date": date})

    @app.post("/api/entities/{entity_id}/rename")
    async def api_entity_rename(request: Request, entity_id: str) -> JSONResponse:
        try:
            dest = await _entity_rename(request, entity_id, json_body=True)
        except CorrectionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status_code)
        return JSONResponse({"ok": True, "id": dest[1], "type": dest[0]})

    @app.post("/api/entities/{entity_id}/merge")
    async def api_entity_merge(request: Request, entity_id: str) -> JSONResponse:
        try:
            dest = await _entity_merge(request, entity_id, json_body=True)
        except CorrectionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status_code)
        return JSONResponse({"ok": True, "id": dest[1], "type": dest[0]})

    return app


def _render(
    request: Request,
    template_name: str,
    *,
    date: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> HTMLResponse:
    session = request.state.session
    report = _report(request)
    today = _today(request) if "today" in template_name else None
    day = _day(request, date) if "days" in template_name else None
    history = _history(request) if "history" in template_name else None
    memory = _memory(request, date) if "memory" in template_name else None
    entities = (
        _entities(request, entity_type, entity_id) if entity_type in {"project", "person"} else None
    )
    status_code = 200
    if entity_id and entities is not None and entities.selected is None:
        status_code = 404
    return _TEMPLATES.TemplateResponse(
        request,
        template_name,
        {
            "status": report,
            "today": today,
            "day": day,
            "history": history,
            "memory": memory,
            "entities": entities,
            "entity_id": entity_id,
            "csrf_token": session.csrf,
        },
        status_code=status_code,
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


def _day(request: Request, date: str | None) -> DayPage:
    config = getattr(request.app.state, "config", None)
    if config is None or not config.db_path.is_file():
        return DayPage(
            id=None,
            date=date,
            state="none",
            error=None,
            choices=(),
            spans=(),
            unverified=(),
            claims=(),
            cited_ids=(),
        )
    try:
        key = _cached_db_key(request.app)
        conn = connect(config.db_path, key, load_vec=False, check_same_thread=False)
    except (KeyProviderError, DatabaseError, sqlcipher.DatabaseError):
        return DayPage(
            id=None,
            date=date,
            state="none",
            error=None,
            choices=(),
            spans=(),
            unverified=(),
            claims=(),
            cited_ids=(),
        )
    try:
        return collect_day_page(conn, date=date)
    except (DatabaseError, sqlcipher.DatabaseError):
        return DayPage(
            id=None,
            date=date,
            state="none",
            error=None,
            choices=(),
            spans=(),
            unverified=(),
            claims=(),
            cited_ids=(),
        )
    finally:
        conn.close()


def _retry_day(request: Request, working_day_id: str) -> str | None:
    from watson.intelligence.runtime import JobFailure, build_runtime, hold_if_unavailable
    from watson.pipeline.consolidation import (
        ConsolidateSettings,
        ConsolidationWait,
        retry_working_day,
    )

    config = getattr(request.app.state, "config", None)
    if config is None or not config.db_path.is_file():
        return None
    key = _cached_db_key(request.app)
    conn = connect(config.db_path, key, load_vec=False, check_same_thread=False)
    try:
        opted = getattr(request.app.state, "runtime", None)
        if opted is not None:
            language = opted
            embedder = opted
        else:
            held = hold_if_unavailable(conn, config.intelligence)
            if held.held:
                language = None
                embedder = None
            else:
                bundle = build_runtime(config)
                language = bundle.language
                embedder = bundle.embedding
        try:
            retry_working_day(
                conn,
                working_day_id,
                runtime=language,
                embedding=embedder,
                settings=ConsolidateSettings.from_config(config),
                spool_dir=config.spool_dir,
            )
        except (JobFailure, ConsolidationWait, DatabaseError, sqlcipher.DatabaseError):
            pass
        row = conn.execute(
            "SELECT date FROM working_days WHERE id = ?", (working_day_id,)
        ).fetchone()
        return None if row is None else str(row["date"])
    finally:
        conn.close()


def _today(request: Request) -> TodayReport:
    conn = _conn(request)
    if conn is None:
        return TodayReport(date=None, segments=())
    try:
        return collect_today(conn)
    except (DatabaseError, sqlcipher.DatabaseError):
        return TodayReport(date=None, segments=())
    finally:
        conn.close()


def _history(request: Request) -> HistoryPage:
    conn = _conn(request)
    if conn is None:
        return HistoryPage(days=())
    try:
        return collect_history(conn)
    except (DatabaseError, sqlcipher.DatabaseError):
        return HistoryPage(days=())
    finally:
        conn.close()


def _entities(request: Request, entity_type: str | None, entity_id: str | None) -> EntityPage:
    kind: EntityType = "person" if entity_type == "person" else "project"
    heading = "People" if kind == "person" else "Projects"
    empty = EntityPage(entity_type=kind, heading=heading, items=(), selected=None)
    conn = _conn(request)
    if conn is None:
        return empty
    try:
        return collect_entities(conn, entity_type=kind, entity_id=entity_id)
    except (DatabaseError, sqlcipher.DatabaseError):
        return empty
    finally:
        conn.close()


def _memory(request: Request, date: str | None) -> MemoryPage:
    conn = _conn(request)
    if conn is None:
        return MemoryPage(date=date, working_day_id=None, choices=(), claims=())
    try:
        return collect_memory(conn, date=date)
    except (DatabaseError, sqlcipher.DatabaseError):
        return MemoryPage(date=date, working_day_id=None, choices=(), claims=())
    finally:
        conn.close()


def _one_claim(request: Request, claim_id: str) -> dict[str, Any] | None:
    conn = _conn(request)
    if conn is None:
        return None
    try:
        item = memory_claim(conn, claim_id)
        if item is None:
            return None
        return {
            "id": item.id,
            "date": item.date,
            "kind": item.kind,
            "original_assertion": item.original_assertion,
            "assertion": item.assertion,
            "original_text": item.original_text,
            "text": item.text,
            "quotes": list(item.quotes),
            "action": item.action,
        }
    except (DatabaseError, sqlcipher.DatabaseError):
        return None
    finally:
        conn.close()


async def _claim_feedback(
    request: Request, claim_id: str, *, json_body: bool = False
) -> str | None:
    action, new_text = await _mutation_fields(request, json_body=json_body)
    conn = _conn(request)
    if conn is None:
        raise CorrectionError("database unavailable")
    try:
        correction = record_claim_correction(
            conn,
            claim_id,
            action,
            new_text=new_text,
            pipeline_version=PIPELINE_VERSION,
        )
        if correction.working_day_id is None:
            return None
        return _date_for_day(conn, correction.working_day_id)
    except (DatabaseError, sqlcipher.DatabaseError) as exc:
        raise CorrectionError("database unavailable") from exc
    finally:
        conn.close()


async def _entity_rename(
    request: Request, entity_id: str, *, json_body: bool = False
) -> tuple[str, str]:
    _action, new_text = await _mutation_fields(request, json_body=json_body, name="new_text")
    conn = _conn(request)
    if conn is None:
        raise CorrectionError("database unavailable")
    try:
        rename_entity(conn, entity_id, new_text or "", pipeline_version=PIPELINE_VERSION)
        return _entity_type_and_id(conn, entity_id)
    except (DatabaseError, sqlcipher.DatabaseError) as exc:
        raise CorrectionError("database unavailable") from exc
    finally:
        conn.close()


async def _entity_merge(
    request: Request, entity_id: str, *, json_body: bool = False
) -> tuple[str, str]:
    _action, into_id = await _mutation_fields(request, json_body=json_body, name="into_id")
    if not into_id:
        raise CorrectionError("merge requires into_id")
    conn = _conn(request)
    if conn is None:
        raise CorrectionError("database unavailable")
    try:
        merge_entities(conn, entity_id, into_id, pipeline_version=PIPELINE_VERSION)
        return _entity_type_and_id(conn, into_id)
    except (DatabaseError, sqlcipher.DatabaseError) as exc:
        raise CorrectionError("database unavailable") from exc
    finally:
        conn.close()


async def _mutation_fields(
    request: Request,
    *,
    json_body: bool,
    name: str = "new_text",
) -> tuple[str, str | None]:
    if json_body:
        try:
            payload = await request.json()
        except Exception as exc:
            raise CorrectionError("invalid json") from exc
        if not isinstance(payload, dict):
            raise CorrectionError("invalid json")
        action = str(payload.get("action") or "")
        raw = payload.get(name)
        if raw is None and name == "new_text":
            raw = payload.get("new_text")
        if raw is None and name == "into_id":
            raw = payload.get("into_id") or payload.get("new_text")
        text = None if raw is None else str(raw)
        return action, text
    cached = request.scope.get("watson_form")
    form = cached if cached is not None else await request.form()
    action = str(form.get("action") or "")
    raw = form.get(name)
    if raw is None and name == "into_id":
        raw = form.get("into_id")
    text = raw if isinstance(raw, str) else None
    return action, text


def _date_for_day(conn: sqlcipher.Connection, working_day_id: str) -> str | None:
    row = conn.execute(
        "SELECT date FROM working_days WHERE id = ?", (working_day_id,)
    ).fetchone()
    return None if row is None else str(row["date"])


def _entity_type_and_id(conn: sqlcipher.Connection, entity_id: str) -> tuple[str, str]:
    row = conn.execute(
        "SELECT type FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        raise CorrectionError("entity not found", status_code=404)
    kind = str(row["type"])
    if kind not in {"project", "person"}:
        raise CorrectionError("entity not found", status_code=404)
    return kind, entity_id


def _entity_path(entity_type: str) -> str:
    return "people" if entity_type == "person" else "projects"


def _conn(request: Request) -> sqlcipher.Connection | None:
    config = getattr(request.app.state, "config", None)
    if config is None or not config.db_path.is_file():
        return None
    try:
        key = _cached_db_key(request.app)
        return connect(config.db_path, key, load_vec=False, check_same_thread=False)
    except (KeyProviderError, DatabaseError, sqlcipher.DatabaseError):
        return None


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
    request.scope["watson_form"] = form
    value = form.get(CSRF_FORM_FIELD)
    return value if isinstance(value, str) else None


def _secure(response: Response) -> Response:
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response

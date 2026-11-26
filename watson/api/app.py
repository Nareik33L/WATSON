"""FastAPI application. The API and the built dashboard share one process."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from watson.api.routes import router
from watson.config import Settings
from watson.db.migrate import migrate
from watson.db.session import make_engine, make_session_factory
from watson.errors import AppError
from watson.seed.demo import seed_demo

log = logging.getLogger("watson.db")


def create_app(settings: Settings) -> FastAPI:
    engine = make_engine(settings.database_path)
    migrate(engine)
    session_factory = make_session_factory(engine)
    with session_factory() as session:
        created = seed_demo(session)
        session.commit()
    if created:
        log.info("Fictional demo portfolio loaded (data_origin=demo)")
    else:
        log.info("Existing local database opened")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(title="Sales Command Centre", version="0.1.0", lifespan=lifespan, docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.state.engine = engine
    app.state.Session = session_factory
    app.state.settings = settings
    app.include_router(router, prefix="/api")

    @app.exception_handler(AppError)
    async def handle_app_error(_request, exc: AppError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    @app.exception_handler(Exception)
    async def handle_unexpected(_request, exc: Exception):
        if isinstance(exc, HTTPException):
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        log.exception("Request failed")
        return JSONResponse(
            status_code=500,
            content={"detail": "Something went wrong. The error was logged locally."},
        )

    dist = settings.frontend_dist

    @app.get("/", include_in_schema=False)
    def dashboard_index():
        return _frontend(dist, "")

    @app.get("/{full_path:path}", include_in_schema=False)
    def dashboard(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found."})
        return _frontend(dist, full_path)

    return app


def _frontend(dist: Path, full_path: str):
    index = dist / "index.html"
    if not index.is_file():
        return HTMLResponse(
            "<p>The dashboard has not been built. From the repository root, run <code>python3 main.py</code>.</p>",
            status_code=503,
        )
    if not full_path or full_path == "/":
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    candidate = (dist / full_path).resolve()
    try:
        candidate.relative_to(dist.resolve())
    except ValueError:
        return JSONResponse(status_code=404, content={"detail": "Not found."})
    if candidate.is_file():
        headers = {"Cache-Control": "no-cache"} if candidate.suffix == ".html" else {}
        return FileResponse(candidate, headers=headers)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})

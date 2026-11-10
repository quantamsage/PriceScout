"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, get_settings
from app.database import create_engine, create_session_factory, init_db
from app.logging_config import configure_logging
from app.repositories.search_repository import SearchRepository
from app.routers import history, home, search
from app.scrapers.browser import BrowserPool
from app.services.search_manager import SearchManager
from app.services.url_safety import Resolver, system_resolver
from app.templating import templates

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' https: http: data:; script-src 'self'; "
        "style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
        "form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def create_app(
    settings: Optional[Settings] = None,
    *,
    transport: Optional[httpx.AsyncBaseTransport] = None,
    resolver: Resolver = system_resolver,
    browser: Optional[BrowserPool] = None,
) -> FastAPI:
    """Build the app. `transport`, `resolver` and `browser` are injection
    points for tests (mocked HTTP, fake DNS, fake browser)."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_engine(settings.database_url)
        await init_db(engine)
        repository = SearchRepository(create_session_factory(engine))
        interrupted = await repository.fail_unfinished_searches()
        if interrupted:
            logger.warning("Marked %d interrupted searches as failed", interrupted)
        manager = SearchManager(
            settings, repository, transport=transport, resolver=resolver, browser=browser
        )
        app.state.engine = engine
        app.state.search_manager = manager
        logger.info("%s started", settings.app_name)
        try:
            yield
        finally:
            await manager.shutdown()
            await engine.dispose()

    app = FastAPI(title=settings.app_name, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"status_code": exc.status_code, "message": _public_message(exc.status_code)},
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/api/"):
            # Keep messages but drop echoed input values.
            errors = [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()]
            return JSONResponse({"detail": errors}, status_code=422)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"status_code": 400, "message": _public_message(400)},
            status_code=400,
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> HTMLResponse:
        # Log the details; never expose them to the user.
        logger.exception("Unhandled error on %s", request.url.path)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"status_code": 500, "message": _public_message(500)},
            status_code=500,
        )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(home.router)
    app.include_router(search.router)
    app.include_router(history.router)
    return app


def _public_message(status_code: int) -> str:
    if status_code == 404:
        return "We couldn't find that page."
    if status_code < 500:
        return "That request couldn't be processed."
    return "Something went wrong on our side. Please try again."


app = create_app()

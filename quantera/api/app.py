"""Platform API application factory. Run: python -m quantera.api  (port 8002)."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from quantera.api.routes import (
    alerts,
    analysis,
    auth,
    collab,
    construction,
    market,
    models_ml,
    paper,
    plans,
    portfolios,
    stocks,
    strategies,
    universes,
)
from quantera.core import errors
from quantera.core.config import Settings, get_settings
from quantera.core.db import Database
from quantera.identity.service import RateLimiter
from quantera.lake.client import LakeClient

log = logging.getLogger("quantera.platform")
VERSION = "0.1.0"


def create_app(settings: Settings | None = None, database: Database | None = None,
               lake: LakeClient | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Quantera Platform API", version=VERSION,
                  description="Research, portfolio and decision-support API. Research signals, "
                              "not investment advice; live trading is disabled.",
                  docs_url=None if settings.environment == "production" else "/docs")
    app.state.settings = settings
    app.state.db = database or Database(settings.database_url, settings.db_schema)
    app.state.limiter = RateLimiter(settings.auth_rate_limit_per_minute)
    app.state.lake = lake or LakeClient(settings.lake_api_url, settings.lake_release)
    errors.install(app)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_credentials=False, allow_methods=["*"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])

    @app.middleware("http")
    async def request_ids(request: Request,
                          call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid[:64]
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        log.info("%s %s %s %.1fms rid=%s", request.method, request.url.path,
                 response.status_code, (time.perf_counter() - start) * 1000, rid)
        return response

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready() -> JSONResponse:
        try:
            with app.state.db.engine.connect() as c:
                c.execute(text("select 1"))
        except Exception as exc:  # noqa: BLE001 - report, do not crash
            log.warning("readiness: database unavailable: %s", type(exc).__name__)
            return JSONResponse({"status": "unavailable", "database": "down"}, 503)
        return JSONResponse({"status": "ok", "database": "up", "version": VERSION})

    v1 = APIRouter(prefix="/api/v1")
    for r in (auth.router, universes.router, analysis.router, stocks.router,
              portfolios.router, construction.router, plans.router,
              strategies.router, models_ml.router, paper.router, alerts.router,
              collab.router, market.router):
        v1.include_router(r)
    app.include_router(v1)
    return app

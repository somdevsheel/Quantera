"""FastAPI application. Run:  python -m api  (or uvicorn api.main:app)."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import errors
from .adapters import derivatives, fundamentals, funds, macro, prices, reference
from .lake import Lake
from .settings import Settings

log = logging.getLogger("quantera.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        lake = Lake(settings)  # raises RELEASE_DRIFT -> the server does not start
        app.state.lake = lake
        log.info("serving release %s in %s mode (%s files verified, %s)", lake.release.tag, settings.mode,
                 lake.drift["files"], lake.drift["mode"])
        yield
        lake.con.close()

    app = FastAPI(title="Quantera Data API", version="1.0.0", lifespan=lifespan,
                  description="Read-only access to a frozen Quantera data-lake release. See docs/api_design.md.")
    errors.install(app)
    for r in (reference.router, prices.router, derivatives.router, funds.router, fundamentals.router, macro.router):
        app.include_router(r)
    return app


app = create_app()

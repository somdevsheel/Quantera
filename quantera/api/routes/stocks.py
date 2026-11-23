"""/api/v1/stocks: search and the canonical research page per instrument."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query

from quantera.api.deps import PrincipalDep, SessionDep
from quantera.api.routes.universes import LakeDep
from quantera.research import service as rs

router = APIRouter(prefix="/stocks", tags=["research"])


@router.get("/search")
def search(p: PrincipalDep, lake: LakeDep, q: Annotated[str, Query(min_length=1, max_length=40)],
           exchange: str | None = None) -> list[dict[str, Any]]:
    """Symbol / name search. Each hit links to its research page route."""
    hits = [h for h in lake.search(q, exchange, 20) if h.get("exchange_mic") in ("XNSE", "XBOM")]
    hits.sort(key=lambda h: h["exchange_mic"] != "XNSE")  # stable: NSE listings first
    return [h | {"route": f"/stocks/{h['exchange_mic']}/{h['listing_symbol']}"} for h in hits]


@router.get("/{exchange}/{symbol}")
def research(exchange: str, symbol: str, p: PrincipalDep, session: SessionDep, lake: LakeDep,
             as_of: date | None = None) -> dict[str, Any]:
    """Overview, technical, volatility, quantitative, fundamentals, valuation, events, risk,
    forecast and options sections, each with its own status and data date."""
    return rs.research(session, p, lake, exchange, symbol, as_of)

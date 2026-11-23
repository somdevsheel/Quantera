"""/api/v1/market/overview: the dashboard's market panel, straight from the frozen lake."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter

from quantera.analysis.scan import latest_market_date
from quantera.api.deps import PrincipalDep
from quantera.api.routes.universes import LakeDep
from quantera.lake.client import LakeClient

router = APIRouter(prefix="/market", tags=["market"])

INDICES = ["NIFTY_50", "NIFTY_NEXT_50", "NIFTY_500", "NIFTY_BANK", "NIFTY_IT",
           "NIFTY_MIDCAP_100", "NIFTY_SMALLCAP_100", "INDIA_VIX"]


def _ret(closes: list[float], back: int) -> float | None:
    return closes[-1] / closes[-1 - back] - 1 if len(closes) > back and closes[-1 - back] else None


def _index(lake: LakeClient, code: str, latest: date) -> dict[str, Any]:
    iid = f"NSEIDX:{code}"
    try:
        rows = [r for r in lake.index_daily(iid, latest - timedelta(days=380), latest)
                if r.get("close")]
    except Exception as e:  # noqa: BLE001 - shown as a per-index failure, not a page failure
        return {"index_id": iid, "status": "FAILED", "message": str(e)[:200]}
    if not rows:
        return {"index_id": iid, "status": "MISSING"}
    c = [float(r["close"]) for r in rows]
    last = rows[-1]
    ytd = [float(r["close"]) for r in rows if str(r["trade_date"])[:4] < str(latest.year)]
    return {
        "index_id": iid, "name": last.get("index_name") or code, "status": "OK",
        "as_of": str(last["trade_date"])[:10], "close": c[-1],
        "change_1d": _ret(c, 1), "change_1m": _ret(c, 21), "change_1y": _ret(c, 250),
        "change_ytd": c[-1] / ytd[-1] - 1 if ytd else None,
        "high_52w": max(c[-252:]), "low_52w": min(c[-252:]),
        "pe": last.get("pe"), "pb": last.get("pb"), "dividend_yield": last.get("dividend_yield"),
        "spark": [{"d": str(r["trade_date"])[:10], "c": float(r["close"])} for r in rows[-63:]],
        "source": last.get("source_file"),
    }


@router.get("/overview")
def overview(p: PrincipalDep, lake: LakeDep) -> dict[str, Any]:
    today = date.today()
    health = lake.health()
    latest = latest_market_date(lake, today)
    stale_days = (today - latest).days
    return {
        "lake_release": health.get("release"), "lake_status": health.get("status"),
        "latest_market_date": latest.isoformat(), "calendar_days_since": stale_days,
        "freshness": "FRESH" if stale_days <= 4 else "STALE",
        "note": "Data come from a frozen lake release; nothing on this page is live.",
        "indices": [_index(lake, c, latest) for c in INDICES],
    }

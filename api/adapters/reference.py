"""Releases, health, catalog, instruments and calendars."""
from datetime import date

import pandas as pd
import pyarrow.parquet as pq
from fastapi import APIRouter, Depends, Query

from ..deps import get_lake
from ..errors import APIError, invalid
from ..identifiers import EXCHANGE_ALIASES, mic, resolve
from ..query import Meta, Page, fetch, page_params, respond, respond_obj, src
from ..release import available
from ..settings import ROOT

router = APIRouter(tags=["reference"])


@router.get("/v1/health")
def health(lake=Depends(get_lake)):
    return respond_obj({"status": "ok", "release": lake.release.tag, "mode": lake.settings.mode,
                        "drift_check_at_startup": lake.drift,
                        "api_cache": {"datasets": sorted(lake.cache), "problem": lake.cache_problem}})


@router.get("/v1/releases")
def releases(lake=Depends(get_lake)):
    return respond_obj({"serving": lake.release.tag,
                        "releases": [{k: m.get(k) for k in ("tag", "frozen_utc", "files", "bytes", "india_audit",
                                                            "forced", "note")} for m in available()]})


@router.get("/v1/releases/{tag}")
def release(tag: str, lake=Depends(get_lake)):
    meta = next((m for m in available() if m["tag"] == tag), None)
    if meta is None:
        raise APIError(404, "UNKNOWN_RELEASE", f"release {tag} not found")
    return respond_obj({"serving": tag == lake.release.tag, "release": meta})


@router.get("/v1/catalog")
def catalog(lake=Depends(get_lake)):
    rows = [{"dataset": d.name, "description": d.description, "files": len(d.files),
             "key_kind": d.key_kind, "first_key": d.files[0][0] if d.files else None,
             "last_key": d.files[-1][0] if d.files else None, "schema_version": d.schema_version,
             "usage_rights": d.usage_rights, "redistributable": d.redistributable,
             "served_in_this_mode": lake.settings.mode != "redistribution" or d.redistributable}
            for d in lake.datasets.values()]
    lake_cat = ROOT / "data/validation/catalog/lake_catalog.csv"
    extra = {}
    if lake_cat.exists():
        extra["lake_catalog"] = "data/validation/catalog/lake_catalog.csv (all Bronze/Silver/Gold datasets)"
    return respond_obj({"release": lake.release.tag, "mode": lake.settings.mode, "datasets": rows, **extra})


@router.get("/v1/catalog/{dataset}/schema")
def schema(dataset: str, lake=Depends(get_lake)):
    d = lake.datasets.get(dataset)
    if d is None or not d.files:
        raise APIError(404, "UNKNOWN_DATASET", f"dataset {dataset} not served")
    s = pq.read_schema(d.files[-1][1])
    return respond_obj({"dataset": dataset, "columns": [{"name": n, "type": str(t)} for n, t in zip(s.names, s.types)]})


@router.get("/v1/instruments")
def instruments(query: str = Query(..., min_length=1), exchange: str | None = None,
                limit: int = Query(50, ge=1, le=1000), lake=Depends(get_lake)):
    q = query.upper()
    el, ins = lake.registry["exchange_listings"], lake.registry["instruments"]
    a = el[el.listing_symbol.str.upper().str.contains(q, regex=False, na=False)
           | el.listing_name.str.upper().str.contains(q, regex=False, na=False)
           | (el.listing_code == query) | (el.instrument_id.str.upper() == q)]
    a = a.assign(source="exchange_listings")[["instrument_id", "exchange_mic", "listing_symbol", "listing_code",
                                              "listing_name", "security_series", "source"]]
    b = ins[ins.canonical_symbol.str.upper().str.contains(q, regex=False, na=False)
            | ins.instrument_name.str.upper().str.contains(q, regex=False, na=False) | (ins.instrument_id == query)]
    b = b.rename(columns={"canonical_symbol": "listing_symbol", "instrument_name": "listing_name"}) \
        .assign(listing_code=None, security_series=None, source="instruments")[a.columns]
    out = pd.concat([a, b], ignore_index=True)
    if exchange:
        out = out[out.exchange_mic == mic(exchange)]
    exact = out.listing_symbol.str.upper() == q
    out = out.assign(_e=~exact).sort_values(["_e", "listing_symbol", "exchange_mic"]).drop(columns="_e").head(limit)
    return respond_obj({"release": lake.release.tag, "count": len(out),
                        "data": out.astype(object).where(out.notna(), None).to_dict("records")})


@router.get("/v1/instruments/resolve")
def resolve_ids(instrument_id: str | None = None, symbol: str | None = None, exchange: str | None = None,
                bse_code: str | None = None, isin: str | None = None, lake=Depends(get_lake)):
    hits = resolve(lake, instrument_id=instrument_id, symbol=symbol, exchange=exchange, bse_code=bse_code, isin=isin)
    return respond_obj({"release": lake.release.tag, "exchange_aliases": EXCHANGE_ALIASES,
                        "data": [h.as_dict() for h in hits]})


@router.get("/v1/calendar/trading")
def trading_calendar(dataset: str = Query("NSE_CM", pattern="^(NSE_CM|NSE_FO|NSE_IDX|BSE_CM)$"),
                     start: date | None = None, end: date | None = None,
                     page: Page = Depends(page_params), lake=Depends(get_lake)):
    d = lake.dataset("india_trading_calendar")
    sql = f"""SELECT CAST(date AS DATE) AS date, dataset, is_trading_day, status FROM {src()}
              WHERE dataset = $ds AND CAST(date AS DATE) BETWEEN $start AND $end"""
    params = {"files": [p for _, p in d.files], "ds": dataset, "start": start or date(1990, 1, 1),
              "end": end or date(2100, 1, 1)}
    return respond(fetch(lake, sql, params, "date", page), page,
                   Meta.of(lake, d, notes=["TRADED = a bhavcopy exists; NO_SESSION_OR_MISSING = weekday without one; "
                                           "HOLIDAY_PUBLISHED = exchange holiday list"]))


@router.get("/v1/calendar/events")
def events(symbol: str | None = None, start: date | None = None, end: date | None = None,
           event_type: str | None = None, page: Page = Depends(page_params), lake=Depends(get_lake)):
    if not (symbol or start or end):
        raise invalid("give symbol and/or a start/end range")
    d = lake.dataset("nse_event_calendar")
    params = {"files": [p for _, p in d.files], "start": start or date(1990, 1, 1), "end": end or date(2100, 1, 1)}
    extra = ""
    if symbol:
        params["sym"] = symbol.upper()
        extra += " AND symbol = $sym"
    if event_type:
        params["et"] = event_type.upper()
        extra += " AND upper(event_type) = $et"
    sql = f"""SELECT CAST(event_date AS DATE) AS event_date, * EXCLUDE (event_date) FROM {src()}
              WHERE CAST(event_date AS DATE) BETWEEN $start AND $end {extra}"""
    return respond(fetch(lake, sql, params, "event_date, symbol, purpose", page), page,
                   Meta.of(lake, d, notes=["is_future reflects the calendar at retrieval, not at event_date"]))

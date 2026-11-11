"""NSE F&O daily, option implied vol / Greeks, and one-day vol surface.

Without start/end these default to the latest trading day in the release, so an
unbounded request never silently scans 26 years of files.
"""
from datetime import date

from fastapi import APIRouter, Depends, Query

from ..deps import get_lake
from ..errors import invalid
from ..query import Meta, Page, fetch, page_params, require_files, respond, src, ymd

router = APIRouter(tags=["derivatives"])
ORDER = "trade_date, expiry_date, strike_price NULLS FIRST, option_type NULLS FIRST, contract_id"


def _range(d, start, end):
    if start is None and end is None:
        last = d.keys[-1]
        return last, last
    return ymd(start), ymd(end)


def _filters(params, expiry, option_type, strike, extra=""):
    sql = ""
    if expiry:
        params["expiry"] = expiry
        sql += " AND CAST(expiry_date AS DATE) = $expiry"
    if option_type:
        params["opt"] = option_type.upper()
        sql += " AND option_type = $opt"
    if strike is not None:
        params["strike"] = strike
        sql += " AND strike_price = $strike"
    return sql + extra


@router.get("/v1/derivatives/fo/daily")
def fo_daily(symbol: str, start: date | None = None, end: date | None = None, expiry: date | None = None,
             instrument_class: str | None = Query(None, pattern="^(FUT|OPT|fut|opt)$"),
             underlying_type: str | None = Query(None, pattern="^(INDEX|STOCK|index|stock)$"),
             option_type: str | None = Query(None, pattern="^(CE|PE|CA|PA|ce|pe|ca|pa)$"),
             strike: float | None = None, page: Page = Depends(page_params), lake=Depends(get_lake)):
    d = lake.dataset("nse_fo_daily")
    lo, hi = _range(d, start, end)
    params = {"files": require_files(d.select(lo, hi)), "sym": symbol.upper()}
    extra = ""
    if instrument_class:
        params["cls"] = instrument_class.upper()
        extra += " AND instrument_class = $cls"
    if underlying_type:
        params["ut"] = underlying_type.upper()
        extra += " AND underlying_type = $ut"
    sql = f"""SELECT CAST(trade_date AS DATE) AS trade_date, CAST(expiry_date AS DATE) AS expiry_date,
                     * EXCLUDE (trade_date, expiry_date)
              FROM {src()} WHERE symbol = $sym {_filters(params, expiry, option_type, strike, extra)}"""
    return respond(fetch(lake, sql, params, ORDER, page), page, Meta.of(lake, d, range=[lo, hi]))


@router.get("/v1/derivatives/options/greeks")
def greeks(symbol: str, start: date | None = None, end: date | None = None, expiry: date | None = None,
           option_type: str | None = Query(None, pattern="^(CE|PE|ce|pe)$"), strike: float | None = None,
           iv_status: str | None = Query(None, description="OK, AMERICAN, EXPIRY_DAY, NO_FORWARD, ..."),
           quality: str | None = Query(None, pattern="^ok_only$"),
           page: Page = Depends(page_params), lake=Depends(get_lake)):
    d = lake.dataset("nse_option_greeks")
    lo, hi = _range(d, start, end)
    params = {"files": require_files(d.select(lo, hi)), "sym": symbol.upper()}
    extra = ""
    if quality == "ok_only":
        iv_status = "OK"
    if iv_status:
        params["st"] = iv_status.upper()
        extra = " AND iv_status = $st"
    sql = f"""SELECT CAST(trade_date AS DATE) AS trade_date, CAST(expiry_date AS DATE) AS expiry_date,
                     * EXCLUDE (trade_date, expiry_date)
              FROM {src()} WHERE symbol = $sym {_filters(params, expiry, option_type, strike, extra)}"""
    return respond(fetch(lake, sql, params, ORDER, page), page,
                   Meta.of(lake, d, range=[lo, hi],
                           notes=["Black-76 on put-call-parity forwards; untraded options are not priced "
                                  "(see docs/india_data_lake.md §4a)"]))


@router.get("/v1/derivatives/options/surface")
def surface(symbol: str, on: date | None = Query(None, alias="date"), otm_only: bool = False,
            page: Page = Depends(page_params), lake=Depends(get_lake)):
    d = lake.dataset("nse_option_greeks")
    key = ymd(on) if on else d.keys[-1]
    files = d.select(key, key)
    if not files:
        raise invalid("no option data on that date", date=str(on))
    otm = " AND ((option_type = 'CE' AND strike_price >= forward) OR (option_type = 'PE' AND strike_price < forward))" \
        if otm_only else ""
    sql = f"""SELECT CAST(trade_date AS DATE) AS trade_date, CAST(expiry_date AS DATE) AS expiry_date, days_to_expiry,
                     forward, strike_price, option_type, moneyness, ln(strike_price / forward) AS log_moneyness,
                     implied_vol, delta, open_interest
              FROM {src()} WHERE symbol = $sym AND iv_status = 'OK' {otm}"""
    tbl = fetch(lake, sql, {"files": files, "sym": symbol.upper()}, "expiry_date, strike_price, option_type", page)
    return respond(tbl, page, Meta.of(lake, d, date=key))

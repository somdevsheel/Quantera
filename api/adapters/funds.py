"""AMFI NAV and US ETF holdings (SEC N-PORT).

ETF holdings are point-in-time: the report returned is the latest report_date among
filings with filing_date <= as_of (and, for that report, the latest amendment filed by then).
"""
from datetime import date

from fastapi import APIRouter, Depends

from ..deps import get_lake
from ..errors import invalid, no_data
from ..query import Meta, Page, fetch, page_params, require_files, respond, src

router = APIRouter(tags=["funds"])


@router.get("/v1/funds/nav")
def nav(scheme_code: str, start: date | None = None, end: date | None = None,
        page: Page = Depends(page_params), lake=Depends(get_lake)):
    d = lake.dataset("amfi_nav")
    files = require_files(d.select(start.strftime("%Y%m") if start else None, end.strftime("%Y%m") if end else None))
    sql = f"""SELECT CAST(nav_date AS DATE) AS nav_date, scheme_code, scheme_name, amc, scheme_type, scheme_category,
                     plan, option, isin_growth_or_payout, isin_reinvestment, nav, nav_raw, quality_flag, currency,
                     source_file
              FROM {src()} WHERE scheme_code = $code
                AND CAST(nav_date AS DATE) BETWEEN $start AND $end"""
    params = {"files": files, "code": scheme_code, "start": start or date(1990, 1, 1), "end": end or date(2100, 1, 1)}
    return respond(fetch(lake, sql, params, "nav_date, source_file", page), page,
                   Meta.of(lake, d, notes=["attribution: Association of Mutual Funds in India (AMFI)"]))


@router.get("/v1/funds/etf-holdings")
def etf_holdings(symbol: str | None = None, series_id: str | None = None, as_of: date | None = None,
                 report_date: date | None = None, page: Page = Depends(page_params), lake=Depends(get_lake)):
    if not (symbol or series_id):
        raise invalid("symbol or series_id is required")
    d = lake.dataset("etf_holdings")
    as_of = as_of or date.fromisoformat(lake.release.frozen_date)
    params = {"files": [p for _, p in d.files], "sym": (symbol or "").upper(), "sid": series_id or "",
              "as_of": as_of}
    rd = ""
    if report_date:
        params["rd"] = report_date
        rd = "AND CAST(report_date AS DATE) = $rd"
    pick_sql = f"""SELECT accession, series_id, symbol, CAST(report_date AS DATE) AS report_date,
                          CAST(filing_date AS DATE) AS filing_date, form
                   FROM {src()} WHERE (symbol = $sym OR series_id = $sid)
                     AND CAST(filing_date AS DATE) <= $as_of {rd}
                   GROUP BY ALL ORDER BY report_date DESC, filing_date DESC, accession DESC LIMIT 1"""
    pick = lake.cursor().execute(pick_sql, params).fetchone()
    if pick is None:
        raise no_data("no N-PORT report filed on or before as_of", as_of=str(as_of))
    acc, sid, sym, rdate, fdate, form = pick
    sql = f"""SELECT CAST(report_date AS DATE) AS report_date, * EXCLUDE (report_date)
              FROM {src()} WHERE accession = $acc"""
    tbl = fetch(lake, sql, {"files": params["files"], "acc": acc}, "holding_row", page)
    return respond(tbl, page, Meta.of(lake, d, as_of=str(as_of),
                                      report={"series_id": sid, "symbol": sym, "report_date": rdate,
                                              "filing_date": fdate, "form": form, "accession": acc}))

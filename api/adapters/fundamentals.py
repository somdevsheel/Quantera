"""Fundamentals: statements (SEC Gold, NSE key financials) and raw XBRL facts.

Point in time: only filings with filing date <= as_of are visible. With latest_only=true
(default) each period returns the newest version filed by as_of. That is recomputed here,
because the lake's own `is_latest_filing` flag looks at all filings, including later ones.
Facts are returned unsummed with their dimensions (handover rule 5); NSE duplicate
facts are flagged (is_duplicate_fact), never dropped.
"""
from datetime import date

from fastapi import APIRouter, Depends, Query

from ..deps import get_lake
from ..errors import invalid, unknown_instrument
from ..query import Meta, Page, fetch, page_params, require_files, respond, src

router = APIRouter(tags=["fundamentals"])
NSE_TS = "strptime(filing_datetime, '%d-%b-%Y %H:%M')"


def _as_of(lake, as_of):
    return as_of or date.fromisoformat(lake.release.frozen_date)


def _sec_ciks(lake, ticker, cik):
    """CIKs for a ticker. A ticker can span several CIKs (e.g. BLK: predecessor
    0001364742 until 2024, then 0002012383); cik_role in the rows says which."""
    if cik:
        return [cik.zfill(10)]
    if not ticker:
        raise invalid("ticker or cik is required for source=sec")
    d = lake.datasets["sec_statements"]
    rows = lake.cursor().execute(f"SELECT DISTINCT cik FROM {src()} WHERE upper(ticker) = $t ORDER BY 1",
                                 {"files": [p for _, p in d.files], "t": ticker.upper()}).fetchall()
    if not rows:
        raise unknown_instrument(ticker=ticker)
    return [r[0] for r in rows]


@router.get("/v1/fundamentals/statements")
def statements(source: str = Query(..., pattern="^(sec|nse)$"), ticker: str | None = None, cik: str | None = None,
               symbol: str | None = None, as_of: date | None = None, latest_only: bool = True,
               period_type: str | None = Query(None, description="NSE: Quarterly | Annual | ..."),
               consolidated: str | None = Query(None, description="NSE: Consolidated | Non-Consolidated"),
               page: Page = Depends(page_params), lake=Depends(get_lake)):
    as_of = _as_of(lake, as_of)
    if source == "sec":
        d = lake.dataset("sec_statements")
        ciks = _sec_ciks(lake, ticker, cik)
        latest = ("QUALIFY row_number() OVER (PARTITION BY cik, period_end, replace(form, '/A', '') "
                  "ORDER BY filing_date DESC, accession_number DESC) = 1") if latest_only else ""
        sql = f"""SELECT CAST(period_end AS DATE) AS period_end, CAST(filing_date AS DATE) AS filing_date,
                         * EXCLUDE (period_end, filing_date)
                  FROM {src()} WHERE list_contains($ciks, cik) AND CAST(filing_date AS DATE) <= $as_of {latest}"""
        params = {"files": [p for _, p in d.files], "ciks": ciks, "as_of": as_of}
        order = "period_end, filing_date, accession_number"
        notes = ["recon_status: BALANCED / BALANCED_DIMENSIONAL_TEMP_EQUITY / UNBALANCED / INCOMPLETE"]
    else:
        if not symbol:
            raise invalid("symbol is required for source=nse")
        d = lake.dataset("nse_key_financials")
        params = {"files": [p for _, p in d.files], "sym": symbol.upper(), "as_of": as_of}
        extra = ""
        if period_type:
            params["pt"] = period_type
            extra += " AND period_type = $pt"
        if consolidated:
            params["cons"] = consolidated
            extra += " AND consolidated = $cons"
        latest = ("QUALIFY row_number() OVER (PARTITION BY symbol, period_type, from_date, to_date, consolidated "
                  "ORDER BY filing_datetime DESC, source_file DESC) = 1") if latest_only else ""
        sql = f"""SELECT CAST(from_date AS DATE) AS from_date, CAST(to_date AS DATE) AS to_date,
                         * EXCLUDE (from_date, to_date, is_latest_filing, filings_for_period)
                  FROM {src()} WHERE symbol = $sym AND filing_datetime < CAST($as_of AS DATE) + INTERVAL 1 DAY {extra}
                  {latest}"""
        order = "to_date, period_type, consolidated, filing_datetime, source_file"
        notes = ["amounts in the units stated by units_note; is_latest_filing is replaced by as-of selection"]
    tbl = fetch(lake, sql, params, order, page)
    return respond(tbl, page, Meta.of(lake, d, as_of=str(as_of), latest_only=latest_only, notes=notes))


@router.get("/v1/fundamentals/facts")
def facts(source: str = Query(..., pattern="^(sec|nse)$"), ticker: str | None = None, cik: str | None = None,
          symbol: str | None = None, accession: str | None = None, concept: str | None = None,
          as_of: date | None = None, page: Page = Depends(page_params), lake=Depends(get_lake)):
    as_of = _as_of(lake, as_of)
    params = {"as_of": as_of}
    extra = ""
    if concept:
        params["concept"] = concept
        extra += " AND concept = $concept"
    if source == "sec":
        d = lake.dataset("sec_filing_facts")
        ciks = _sec_ciks(lake, ticker, cik)
        params["files"] = require_files(d.select(keys=set(ciks)), cik=ciks)
        if accession:
            params["acc"] = accession
            extra += " AND accession_number = $acc"
        sql = f"SELECT * FROM {src()} WHERE CAST(filing_date AS DATE) <= $as_of {extra}"
        order = "filing_date, accession_number, concept, context_id, unit"
    else:
        if not symbol:
            raise invalid("symbol is required for source=nse")
        d = lake.dataset("nse_xbrl_facts")
        params["files"] = require_files(d.select(keys={symbol.upper()}), symbol=symbol)
        if accession:
            params["acc"] = accession
            extra += " AND source_file LIKE '%' || $acc || '%'"
        sql = f"""SELECT {NSE_TS} AS filing_ts, * FROM {src()}
                  WHERE {NSE_TS} < CAST($as_of AS DATE) + INTERVAL 1 DAY {extra}"""
        order = "filing_ts, source_file, fact_seq"
    tbl = fetch(lake, sql, params, order, page)
    return respond(tbl, page, Meta.of(lake, d, as_of=str(as_of),
                                      notes=["facts are unsummed; dimensional members are never rolled up"]))

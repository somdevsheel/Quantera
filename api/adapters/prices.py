"""Daily prices (NSE, BSE, global), NSE indices and corporate actions.

Point-in-time adjustment: `adjust=split` multiplies each row by the product of the
combined price factors of events with trade_date < ex_date <= as_of. Events after
`as_of` are never applied. India factors cover splits and bonuses only (no dividends),
so `adjust=total` is refused rather than approximated.
"""
from datetime import date

from fastapi import APIRouter, Depends, Query

from ..deps import get_lake
from ..errors import invalid
from ..identifiers import resolve_one
from ..query import Meta, Page, fetch, page_params, require_files, respond, src, ymd

router = APIRouter(tags=["prices"])

PRICE_COLS = ("open", "high", "low", "close", "last_price")


def _as_of(lake, as_of):
    return as_of or date.fromisoformat(lake.release.frozen_date)


def _adjusted(base_sql, factor_sql, event_cond):
    """Wrap a price query with a per-row factor = product of f.pf over events matching
    `event_cond` (SQL on f.ex_date / p.trade_date). Prices are multiplied, volume divided."""
    adj = ", ".join(f"{c} * f AS {c}" for c in PRICE_COLS)
    return f"""
        WITH f AS ({factor_sql}),
             p AS ({base_sql}),
             pf AS (SELECT p.*, coalesce((SELECT exp(sum(ln(f.pf))) FROM f WHERE {event_cond}), 1.0) AS f FROM p)
        SELECT * EXCLUDE (f) REPLACE ({adj}, CAST(round(volume / f) AS BIGINT) AS volume), f AS adj_factor
        FROM pf"""


@router.get("/v1/prices/daily")
def prices_daily(instrument_id: str | None = None, symbol: str | None = None, exchange: str | None = None,
                 bse_code: str | None = None, isin: str | None = None,
                 start: date | None = None, end: date | None = None,
                 adjust: str = Query("none", pattern="^(none|split|total)$"),
                 as_of: date | None = None, series: str | None = Query(None, description="NSE/BSE series, e.g. EQ"),
                 page: Page = Depends(page_params), lake=Depends(get_lake)):
    if adjust == "total":
        raise invalid("adjust=total is not available: the release has no dividend factors for these sources",
                      supported=["none", "split"])
    lst = resolve_one(lake, instrument_id=instrument_id, symbol=symbol, exchange=exchange, bse_code=bse_code, isin=isin)
    as_of = _as_of(lake, as_of)
    params = {"start": start or date(1990, 1, 1), "end": end or date(2100, 1, 1), "iid": lst.instrument_id,
              "as_of": as_of}
    notes = []
    series_sql = ""
    if series:
        params["series"] = series.upper()
        series_sql = "AND security_series = $series"

    if lst.exchange_mic == "XNSE" and lst.source == "exchange_listings":
        d = lake.dataset("nse_equity_daily")
        params["files"] = require_files(d.select(ymd(start), ymd(end)))
        if "nse_equity_daily" in lake.cache:   # same rows, sorted by lookup_key (api/cache.py)
            params["files"] = [lake.cache["nse_equity_daily"]]
            params["k_id"], params["k_sym"] = lst.instrument_id, "NSESYM:" + lst.symbol
            match = "lookup_key IN ($k_id, $k_sym)"
        else:
            params["sym"] = lst.symbol
            match = "(instrument_id = $iid OR (instrument_id IS NULL AND symbol = $sym))"
        base = f"""
            SELECT CAST(trade_date AS DATE) AS trade_date, $iid AS instrument_id, symbol, security_series, isin,
                   'XNSE' AS exchange_mic, currency, open, high, low, close, last_price, previous_close, volume,
                   traded_value, CAST(trade_count AS BIGINT) AS trade_count,
                   CASE WHEN instrument_id IS NULL THEN 'SYMBOL_PRE_ISIN' ELSE 'INSTRUMENT_ID' END AS id_match
            FROM {src()}
            WHERE {match} AND CAST(trade_date AS DATE) BETWEEN $start AND $end {series_sql}"""
        notes.append("NSE bhavcopy before mid-2011 has no ISIN; those rows are matched on the current symbol "
                     "and flagged id_match=SYMBOL_PRE_ISIN")
        dsets = [d]
    elif lst.exchange_mic == "XBOM" and lst.source == "exchange_listings":
        d = lake.dataset("bse_equity_daily")
        params["files"] = require_files(d.select(ymd(start), ymd(end)))
        params["code"] = lst.code
        code_col = "bse_scrip_code"
        if "bse_equity_daily" in lake.cache:
            params["files"] = [lake.cache["bse_equity_daily"]]
            code_col = "lookup_key"
        base = f"""
            SELECT CAST(trade_date AS DATE) AS trade_date, $iid AS instrument_id, bse_scrip_code, bse_symbol AS symbol,
                   security_series, isin, exchange_mic, currency, open, high, low, close, last_price, previous_close,
                   volume, turnover AS traded_value, trade_count,
                   CASE WHEN instrument_id IS NULL THEN 'SCRIP_CODE' ELSE 'INSTRUMENT_ID' END AS id_match,
                   source_repair, observation_id, source_file
            FROM {src()}
            WHERE {code_col} = $code AND CAST(trade_date AS DATE) BETWEEN $start AND $end {series_sql}"""
        dsets = [d]
    else:
        d = lake.dataset("global_equity_daily")
        params["files"] = require_files(d.select(keys={lst.symbol}), symbol=lst.symbol)
        if series:
            raise invalid("series applies to NSE/BSE only")
        base = f"""
            SELECT CAST(trading_date AS DATE) AS trade_date, instrument_id, symbol, '{lst.exchange_mic}' AS exchange_mic,
                   open, high, low, close, NULL::DOUBLE AS last_price, adj_close, CAST(volume AS BIGINT) AS volume,
                   dividends, stock_splits, price_adjustment_basis, provider
            FROM {src()}
            WHERE CAST(trading_date AS DATE) BETWEEN $start AND $end"""
        dsets = [d]

    if lst.source == "exchange_listings":
        if adjust == "split":
            ca = lake.dataset("india_corporate_actions")
            params["ca"] = [p for _, p in ca.files]
            factor = f"""SELECT DISTINCT CAST(ex_date AS DATE) AS ex_date, combined_price_factor AS pf
                         FROM {src('ca')} WHERE instrument_id = $iid"""
            base = _adjusted(base, factor, "f.ex_date > p.trade_date AND f.ex_date <= $as_of")
            dsets.append(ca)
            notes.append(f"split/bonus adjusted with events up to as_of={as_of}; previous_close is as reported")
        else:
            params.pop("as_of")
    else:
        # Yahoo OHLC/volume arrive already split-adjusted for every split up to retrieval
        # (the Silver label UNADJUSTED_OHLC_WITH_ADJ_CLOSE is inaccurate). Undo the splits
        # we must not apply: all of them for adjust=none, those after as_of for adjust=split.
        ca = lake.dataset("global_corporate_actions")
        params["ca"] = [p for _, p in ca.files]
        params["sym"] = lst.symbol
        factor = f"""SELECT CAST(ex_date AS DATE) AS ex_date, exp(-sum(ln(price_factor))) AS pf
                     FROM {src('ca')} WHERE symbol = $sym AND action_type = 'SPLIT' AND price_factor > 0 GROUP BY 1"""
        cond = "f.ex_date > p.trade_date" + (" AND f.ex_date > $as_of" if adjust == "split" else "")
        if adjust != "split":
            params.pop("as_of")
        base = _adjusted(base, factor, cond)
        dsets.append(ca)
        notes.append("provider prices are split-adjusted at retrieval; the API reverses that with the release's "
                     "split table" + (f" and re-applies splits up to as_of={as_of}" if adjust == "split" else
                                      " to return as-traded prices") + "; adj_close is the provider's (incl. dividends)")
    tbl = fetch(lake, base, params, "trade_date" + (", security_series" if lst.source == "exchange_listings" else ""),
                page)
    return respond(tbl, page, Meta.of(lake, *dsets, notes=notes, instrument=lst.as_dict(), adjust=adjust,
                                      as_of=str(as_of) if adjust == "split" else None))


@router.get("/v1/indices")
def indices_list(lake=Depends(get_lake)):
    d = lake.dataset("nse_indices_daily")
    sql = f"SELECT index_id, any_value(index_name) AS index_name, max(CAST(trade_date AS DATE)) AS last_date FROM {src()} GROUP BY 1"
    return respond(fetch(lake, sql, {"files": d.select(d.keys[-1][:4] + "0101")}, "index_id", Page(10_000, 0, "json")),
                   Page(10_000, 0, "json"), Meta.of(lake, d))


@router.get("/v1/indices/daily")
def indices_daily(index_id: str, start: date | None = None, end: date | None = None,
                  page: Page = Depends(page_params), lake=Depends(get_lake)):
    d = lake.dataset("nse_indices_daily")
    files = require_files(d.select(ymd(start), ymd(end)))
    sql = f"""SELECT CAST(trade_date AS DATE) AS trade_date, * EXCLUDE (trade_date) FROM {src()}
              WHERE index_id = $idx"""
    return respond(fetch(lake, sql, {"files": files, "idx": index_id}, "trade_date", page), page, Meta.of(lake, d))


@router.get("/v1/corporate-actions")
def corporate_actions(instrument_id: str | None = None, symbol: str | None = None, exchange: str | None = None,
                      bse_code: str | None = None, isin: str | None = None, as_of: date | None = None,
                      page: Page = Depends(page_params), lake=Depends(get_lake)):
    lst = resolve_one(lake, instrument_id=instrument_id, symbol=symbol, exchange=exchange, bse_code=bse_code, isin=isin)
    as_of = _as_of(lake, as_of)
    if lst.source == "exchange_listings":
        d = lake.dataset("india_corporate_actions")
        sql = f"""SELECT * REPLACE (CAST(ex_date AS DATE) AS ex_date) FROM {src()}
                  WHERE instrument_id = $key AND CAST(ex_date AS DATE) <= $as_of"""
        key = lst.instrument_id
    else:
        d = lake.dataset("global_corporate_actions")
        sql = f"""SELECT * REPLACE (CAST(ex_date AS DATE) AS ex_date) FROM {src()}
                  WHERE symbol = $key AND CAST(ex_date AS DATE) <= $as_of"""
        key = lst.symbol
    tbl = fetch(lake, sql, {"files": [p for _, p in d.files], "key": key, "as_of": as_of}, "ex_date", page)
    return respond(tbl, page, Meta.of(lake, d, instrument=lst.as_dict(), as_of=str(as_of),
                                      notes=["events with ex_date after as_of are not returned "
                                             "(announcement dates are not in the release)"]),
                   empty_is_404=False)

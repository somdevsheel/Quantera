"""Daily prices (NSE, BSE, global), NSE indices and corporate actions.

Identity: an NSE instrument returns the full history of its security - earlier ISINs (most
face-value splits issue a new ISIN), earlier symbols (renames) and pre-2011 rows that carry
no ISIN - from gold/india/reference/nse_security_id_history. `id_match` on every row says
how it was linked: INSTRUMENT_ID, LINKED_OTHER_ISIN or LINKED_PRE_ISIN.

Point-in-time adjustment multiplies each row by the product of factors of events with
trade_date < ex_date <= as_of; events after `as_of` are never applied.
  adjust=split  split + bonus factors (all ISINs of the security)
  adjust=total  split + bonus + cash dividends (factor (P-D)/P, P = close before ex-date;
                only dividend rows with apply=true, see nse_dividend_factors)
Corporate actions come from NSE, so BSE-only listings and global symbols have no dividend
factors; adjust=total is refused there rather than silently returning split-only prices.
"""
from datetime import date

from fastapi import APIRouter, Depends, Query

from ..cache import NSE_KEYED
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


def india_factor_sql(lake, params, dsets, isins, security_key, total):
    """Factor rows (ex_date, pf) for an Indian security: split/bonus over all its ISINs,
    plus applied cash-dividend factors when `total`."""
    ca = lake.dataset("india_corporate_actions")
    params["ca"] = [p for _, p in ca.files]
    params["isins"] = isins
    dsets.append(ca)
    parts = [f"""SELECT DISTINCT CAST(ex_date AS DATE) AS ex_date, combined_price_factor AS pf
                 FROM {src('ca')} WHERE list_contains($isins, instrument_id)"""]
    if total:
        dv = lake.dataset("nse_dividend_factors")
        params["dv"] = [p for _, p in dv.files]
        params["sk"] = security_key
        dsets.append(dv)
        parts.append(f"""SELECT CAST(ex_date AS DATE) AS ex_date, dividend_factor AS pf
                         FROM {src('dv')} WHERE security_key = $sk AND apply""")
    return " UNION ALL ".join(parts)


@router.get("/v1/prices/daily")
def prices_daily(instrument_id: str | None = None, symbol: str | None = None, exchange: str | None = None,
                 bse_code: str | None = None, isin: str | None = None,
                 start: date | None = None, end: date | None = None,
                 adjust: str = Query("none", pattern="^(none|split|total)$"),
                 as_of: date | None = None, series: str | None = Query(None, description="NSE/BSE series, e.g. EQ"),
                 page: Page = Depends(page_params), lake=Depends(get_lake)):
    lst = resolve_one(lake, instrument_id=instrument_id, symbol=symbol, exchange=exchange, bse_code=bse_code, isin=isin)
    as_of = _as_of(lake, as_of)
    params = {"start": start or date(1990, 1, 1), "end": end or date(2100, 1, 1), "iid": lst.instrument_id,
              "as_of": as_of}
    notes, extra = [], {}
    series_sql = ""
    if series:
        params["series"] = series.upper()
        series_sql = "AND security_series = $series"
    india = lst.source in ("exchange_listings", "nse_identity_history")
    sk, segs = lake.nse_security(instrument_id=lst.instrument_id) if india else (None, None)
    isins = sorted(set(segs.instrument_id.dropna())) if segs is not None else [lst.instrument_id]
    if segs is not None:
        extra["security"] = {"security_key": sk, "isins": isins, "symbols": list(dict.fromkeys(segs.symbol)),
                             "history_from": str(segs.first_date.min())}

    if lst.exchange_mic == "XNSE" and india:
        d = lake.dataset("nse_equity_daily")
        params["files"] = require_files(d.select(ymd(start), ymd(end)))
        params["sk"] = sk or lst.instrument_id
        if "nse_equity_daily" in lake.cache:   # same rows as NSE_KEYED, sorted by lookup_key (api/cache.py)
            params["files"] = [lake.cache["nse_equity_daily"]]
            source = src()
        else:
            params["hist"] = [p for _, p in lake.datasets["nse_security_id_history"].files]
            source = f"({NSE_KEYED.format(files='$files', hist='$hist')})"
        base = f"""
            SELECT CAST(trade_date AS DATE) AS trade_date, $iid AS instrument_id, symbol, security_series, isin,
                   'XNSE' AS exchange_mic, currency, open, high, low, close, last_price, previous_close, volume,
                   traded_value, CAST(trade_count AS BIGINT) AS trade_count,
                   CASE WHEN instrument_id = $iid THEN 'INSTRUMENT_ID' WHEN instrument_id IS NULL THEN 'LINKED_PRE_ISIN'
                        ELSE 'LINKED_OTHER_ISIN' END AS id_match
            FROM {source}
            WHERE lookup_key = $sk AND CAST(trade_date AS DATE) BETWEEN $start AND $end {series_sql}"""
        notes.append("full security history: earlier ISINs/symbols and pre-2011 rows (no ISIN in the bhavcopy) "
                     "are linked via the identity history; see id_match and `symbol` on each row")
        dsets = [d, lake.datasets["nse_security_id_history"]]
    elif lst.exchange_mic == "XBOM" and india:
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

    if india:
        if adjust == "total" and sk is None:
            raise invalid("adjust=total needs NSE corporate actions; this listing is not traded on NSE "
                          "(BSE-only), so no dividend factors exist", supported=["none", "split"])
        if adjust in ("split", "total"):
            factor = india_factor_sql(lake, params, dsets, isins, sk, adjust == "total")
            base = _adjusted(base, factor, "f.ex_date > p.trade_date AND f.ex_date <= $as_of")
            notes.append(f"{'split/bonus + cash dividend' if adjust == 'total' else 'split/bonus'} adjusted with "
                         f"events up to as_of={as_of}; previous_close is as reported")
        else:
            params.pop("as_of")
    else:
        if adjust == "total":
            raise invalid("adjust=total is not available for global symbols (no dividend factors in the release); "
                          "adj_close is the provider's dividend-adjusted close", supported=["none", "split"])
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
    tbl = fetch(lake, base, params, "trade_date" + (", security_series" if india else ""), page)
    return respond(tbl, page, Meta.of(lake, *dsets, notes=notes, instrument=lst.as_dict(), adjust=adjust,
                                      as_of=str(as_of) if adjust != "none" else None, **extra))


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
    if lst.source in ("exchange_listings", "nse_identity_history"):
        d = lake.dataset("india_corporate_actions")
        _, segs = lake.nse_security(instrument_id=lst.instrument_id)
        keys = sorted(set(segs.instrument_id.dropna())) if segs is not None else [lst.instrument_id]
        sql = f"""SELECT * REPLACE (CAST(ex_date AS DATE) AS ex_date) FROM {src()}
                  WHERE list_contains($keys, instrument_id) AND CAST(ex_date AS DATE) <= $as_of"""
    else:
        d = lake.dataset("global_corporate_actions")
        sql = f"""SELECT * REPLACE (CAST(ex_date AS DATE) AS ex_date) FROM {src()}
                  WHERE list_contains($keys, symbol) AND CAST(ex_date AS DATE) <= $as_of"""
        keys = [lst.symbol]
    tbl = fetch(lake, sql, {"files": [p for _, p in d.files], "keys": keys, "as_of": as_of}, "ex_date", page)
    return respond(tbl, page, Meta.of(lake, d, instrument=lst.as_dict(), as_of=str(as_of),
                                      notes=["events with ex_date after as_of are not returned "
                                             "(announcement dates are not in the release)"]),
                   empty_is_404=False)


@router.get("/v1/corporate-actions/dividends")
def dividends(instrument_id: str | None = None, symbol: str | None = None, exchange: str | None = None,
              bse_code: str | None = None, isin: str | None = None, as_of: date | None = None,
              include_unapplied: bool = True, page: Page = Depends(page_params), lake=Depends(get_lake)):
    lst = resolve_one(lake, instrument_id=instrument_id, symbol=symbol, exchange=exchange, bse_code=bse_code, isin=isin)
    sk, _ = lake.nse_security(instrument_id=lst.instrument_id) if lst.source in ("exchange_listings", "nse_identity_history") else (None, None)
    if sk is None:
        raise invalid("dividends are available for NSE-traded Indian securities only")
    as_of = _as_of(lake, as_of)
    d = lake.dataset("nse_dividend_factors")
    sql = f"""SELECT * REPLACE (CAST(ex_date AS DATE) AS ex_date) FROM {src()}
              WHERE security_key = $sk AND CAST(ex_date AS DATE) <= $as_of {'' if include_unapplied else 'AND apply'}"""
    tbl = fetch(lake, sql, {"files": [p for _, p in d.files], "sk": sk, "as_of": as_of}, "ex_date", page)
    return respond(tbl, page, Meta.of(lake, d, instrument=lst.as_dict(), as_of=str(as_of), security_key=sk,
                                      notes=["dividend_factor = (prior_close - dividend_cash_inr) / prior_close; "
                                             "only rows with apply=true are used by adjust=total"]),
                   empty_is_404=False)

"""NSE cash-dividend adjustment factors for total-return prices (Gold).

Input  : data/silver/india/corporate_actions/nse_corporate_actions.parquet  (dividend_cash_inr, v2 parser)
         data/silver/india/equity_prices/nse_*equity_prices_YYYYMMDD.parquet (raw closes)
Gold   : data/gold/india/corporate_actions/nse_dividend_factors.parquet
Ledger : data/validation/india/nse_dividend_factors_summary.json

Method (CRSP-style, documented in docs/india_data_lake.md §4b):
  For every (ISIN, ex_date): D = sum of cash dividends per share across that day's records
  (final + interim + special are separate records). P = last NSE close strictly before
  ex_date (EQ series preferred, else any series; pre-2011 rows without ISIN are matched on
  the record's symbol). dividend_factor = (P - D) / P, applied multiplicatively to prices
  strictly before ex_date, together with the split/bonus factors.
Statuses (rows are never dropped; `apply` says whether the API uses the factor):
  OK                 applied
  NO_PRIOR_CLOSE     no NSE close in the 30 calendar days before ex_date
  STALE_PRIOR_CLOSE  prior close more than 10 calendar days before ex_date (not applied)
  IMPLAUSIBLE        D >= 50% of P - almost always a parse/unit error (not applied)
Dividend records the parser could not use (UNPARSED, AMBIGUOUS_*, UNSUPPORTED_SERIES for
REIT/InvIT) are listed with status PARSE_<method> and not applied. NIL dividends are skipped.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
CORPACT = ROOT / "data/silver/india/corporate_actions/nse_corporate_actions.parquet"
PRICES = str(ROOT / "data/silver/india/equity_prices/nse_*equity_prices_*.parquet")
GOLD = ROOT / "data/gold/india/corporate_actions/nse_dividend_factors.parquet"
SUMMARY = ROOT / "data/validation/india/nse_dividend_factors_summary.json"
SCHEMA_VERSION = "nse_dividend_factors_v1"
IDHIST = ROOT / "data/gold/india/reference/nse_security_id_history.parquet"
USABLE = ("RS_AMOUNT", "PCT_OF_FV")
MAX_GAP_DAYS, LOOKBACK_DAYS, MAX_YIELD = 10, 30, 0.5


def build():
    con = duckdb.connect(config={"threads": 8, "memory_limit": "12GB"})
    con.execute(f"""
        CREATE TEMP TABLE ev AS
        SELECT isin, CAST(ex_date AS DATE) AS ex_date,
               any_value(instrument_id) AS instrument_id, any_value(symbol) AS symbol,
               sum(dividend_cash_inr) FILTER (WHERE dividend_parse_method IN {USABLE}) AS dividend_cash_inr,
               count(*) AS n_records,
               string_agg(subject, ' | ' ORDER BY subject) AS subjects,
               string_agg(DISTINCT dividend_parse_method, ',') AS parse_methods,
               bool_or(dividend_parse_method NOT IN {USABLE}) AS has_unusable,
               string_agg(DISTINCT source_file, ',') AS source_files
        FROM read_parquet('{CORPACT}')
        WHERE action_flags LIKE '%DIVIDEND%' AND ex_date IS NOT NULL AND isin IS NOT NULL
          AND dividend_parse_method <> 'NIL' AND dividend_parse_method <> 'NOT_DIVIDEND'
        GROUP BY 1, 2""")
    # Every price row and every event is mapped to its security_key from the identity
    # history (india_security_id_history.py): ISIN changes, symbol renames and the pre-2011
    # era without ISINs are one security. Events: by ISIN, else by the symbol segment that
    # covers the ex-date.
    con.execute(f"CREATE TEMP TABLE seg AS SELECT * FROM read_parquet('{IDHIST}')")
    con.execute(f"""
        CREATE TEMP TABLE px AS
        SELECT coalesce(si.security_key, ss.security_key) AS security_key, p.instrument_id, p.security_series,
               CAST(p.trade_date AS DATE) AS trade_date, p.close
        FROM read_parquet('{PRICES}', union_by_name=true) p
        LEFT JOIN (SELECT DISTINCT instrument_id, security_key FROM seg WHERE instrument_id IS NOT NULL) si
               ON p.instrument_id = si.instrument_id
        LEFT JOIN (SELECT symbol, first_date, last_date, security_key FROM seg WHERE instrument_id IS NULL) ss
               ON p.instrument_id IS NULL AND p.symbol = ss.symbol
              AND CAST(p.trade_date AS DATE) BETWEEN ss.first_date AND ss.last_date
        WHERE p.close > 0""")
    con.execute(f"""
        CREATE TEMP TABLE evk AS
        SELECT ev.*, coalesce(si.security_key, ss.security_key) AS security_key,
               CASE WHEN si.security_key IS NOT NULL THEN 'ISIN' WHEN ss.security_key IS NOT NULL THEN 'SYMBOL_AT_EX_DATE' END AS key_match
        FROM ev
        LEFT JOIN (SELECT DISTINCT instrument_id, security_key FROM seg WHERE instrument_id IS NOT NULL) si
               ON si.instrument_id = 'IN:' || ev.isin
        LEFT JOIN (SELECT symbol, any_value(security_key) AS security_key, min(first_date) AS f, max(last_date) AS l
                   FROM seg GROUP BY symbol, security_key) ss
               ON si.security_key IS NULL AND ss.symbol = ev.symbol AND ev.ex_date > ss.f AND ev.ex_date <= ss.l + INTERVAL {LOOKBACK_DAYS} DAY""")
    # Prior close: EQ series within {MAX_GAP_DAYS} days if any, else any series.
    con.execute(f"""
        CREATE TEMP TABLE cand AS
        WITH eq AS (SELECT e.security_key, e.ex_date, p.trade_date, p.close, p.security_series, p.instrument_id
                    FROM (SELECT DISTINCT security_key, ex_date FROM evk WHERE security_key IS NOT NULL) e
                    ASOF JOIN (SELECT * FROM px WHERE security_series = 'EQ') p
                      ON e.security_key = p.security_key AND p.trade_date < e.ex_date),
             anyser AS (SELECT e.security_key, e.ex_date, p.trade_date, p.close, p.security_series, p.instrument_id
                        FROM (SELECT DISTINCT security_key, ex_date FROM evk WHERE security_key IS NOT NULL) e
                        ASOF JOIN px p ON e.security_key = p.security_key AND p.trade_date < e.ex_date),
             pick AS (SELECT a.security_key, a.ex_date,
                             (e.trade_date IS NOT NULL AND a.ex_date - e.trade_date <= {MAX_GAP_DAYS}) AS use_eq,
                             a.trade_date AS a_d, a.close AS a_c, a.security_series AS a_s, a.instrument_id AS a_i,
                             e.trade_date AS e_d, e.close AS e_c, e.security_series AS e_s, e.instrument_id AS e_i
                      FROM anyser a LEFT JOIN eq e USING (security_key, ex_date))
        SELECT security_key, ex_date,
               CASE WHEN use_eq THEN e_d ELSE a_d END AS prior_trade_date,
               CASE WHEN use_eq THEN e_c ELSE a_c END AS prior_close,
               CASE WHEN use_eq THEN e_s ELSE a_s END AS prior_series,
               CASE WHEN use_eq THEN e_i ELSE a_i END AS prior_instrument_id
        FROM pick""")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = GOLD.with_suffix(".parquet.tmp")
    GOLD.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"""
        COPY (
        WITH j AS (
            SELECT evk.*, c.prior_trade_date, c.prior_close, c.prior_series,
                   CASE WHEN c.prior_close IS NULL THEN NULL
                        WHEN c.prior_instrument_id = 'IN:' || evk.isin THEN 'SAME_ISIN'
                        WHEN c.prior_instrument_id IS NULL THEN 'LINKED_PRE_ISIN'
                        ELSE 'LINKED_OTHER_ISIN' END AS price_match
            FROM evk LEFT JOIN cand c USING (security_key, ex_date))
        SELECT coalesce(instrument_id, 'IN:' || isin) AS instrument_id, security_key, key_match, isin, symbol, ex_date,
               dividend_cash_inr, n_records, subjects, parse_methods, prior_trade_date, prior_close, prior_series,
               price_match, dividend_cash_inr / prior_close AS dividend_yield,
               (prior_close - dividend_cash_inr) / prior_close AS dividend_factor,
               CASE WHEN dividend_cash_inr IS NULL THEN 'PARSE_' || replace(parse_methods, ',', '_')
                    WHEN security_key IS NULL THEN 'NO_SECURITY_MATCH'
                    WHEN prior_close IS NULL OR ex_date - prior_trade_date > {LOOKBACK_DAYS} THEN 'NO_PRIOR_CLOSE'
                    WHEN ex_date - prior_trade_date > {MAX_GAP_DAYS} THEN 'STALE_PRIOR_CLOSE'
                    WHEN dividend_cash_inr / prior_close >= {MAX_YIELD} THEN 'IMPLAUSIBLE'
                    ELSE 'OK' END AS status,
               has_unusable AS partial_parse,
               source_files, '{SCHEMA_VERSION}' AS schema_version, '{now}' AS computed_utc
        FROM j)
        TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)""")
    con.execute(f"""COPY (SELECT *, status = 'OK' AS apply FROM read_parquet('{tmp}') ORDER BY isin, ex_date)
                    TO '{GOLD}' (FORMAT parquet, COMPRESSION zstd)""")
    tmp.unlink()
    s = dict(con.execute(f"SELECT status, count(*) FROM read_parquet('{GOLD}') GROUP BY 1 ORDER BY 2 DESC").fetchall())
    stats = con.execute(f"""SELECT count(*) FILTER (WHERE apply), median(dividend_yield) FILTER (WHERE apply),
                                   quantile_cont(dividend_yield, 0.99) FILTER (WHERE apply),
                                   count(*) FILTER (WHERE apply AND partial_parse),
                                   count(*) FILTER (WHERE apply AND price_match <> 'SAME_ISIN')
                            FROM read_parquet('{GOLD}')""").fetchone()
    summary = {"computed_utc": now, "schema_version": SCHEMA_VERSION, "events": sum(s.values()), "status": s,
               "applied": stats[0], "median_yield": stats[1], "p99_yield": stats[2],
               "applied_with_partial_parse": stats[3], "applied_via_linked_history": stats[4]}
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(summary, indent=2, default=float))
    print(json.dumps(summary, indent=2, default=float))


if __name__ == "__main__":
    build()

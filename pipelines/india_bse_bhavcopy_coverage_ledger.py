"""BSE bhavcopy coverage ledger (one row per scrip x month), in the style of
india_bse_historical_coverage.py, plus delivery-enrichment status from the
per-scrip API dataset.

Output: data/silver/reference/india/bse_bhavcopy_coverage.csv
Columns:
  bse_scrip_code, period, exchange_trading_days, days_with_data, coverage_ratio,
  first_trade_date, last_trade_date, registry_status, instrument_id,
  exchange_listing_id, company_name, bse_symbol, isin, security_series,
  quarantined_rows, delivery_days (API dataset), delivery_status
delivery_status: ENRICHED (API rows for every bhavcopy day), PARTIAL, NOT_ENRICHED.

  python pipelines/india_bse_bhavcopy_coverage_ledger.py [--start YYYY-MM --end YYYY-MM] [--codes a,b]
"""
import argparse
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BHAV = ROOT / "data/silver/india/equity_prices/bse_bhavcopy/year=*/month=*/bse_bhavcopy_????????.parquet"
API = ROOT / "data/silver/india/equity_prices/bse/year=*/month=*/*.parquet"
QUAR = ROOT / "data/quarantine/india/equity_prices/bse_bhavcopy/year=*/month=*/*_quarantine.parquet"
OUTPUT = ROOT / "data/silver/reference/india/bse_bhavcopy_coverage.csv"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="0000-01")
    p.add_argument("--end", default="9999-12")
    p.add_argument("--codes")
    a = p.parse_args()

    where = f"strftime(trade_date, '%Y-%m') BETWEEN '{a.start}' AND '{a.end}'"
    if a.codes:
        codes = ",".join(f"'{c.strip()}'" for c in a.codes.split(",") if c.strip())
        where += f" AND bse_scrip_code IN ({codes})"

    con = duckdb.connect()
    con.execute(f"CREATE VIEW bhav AS SELECT * FROM read_parquet('{BHAV}', union_by_name=true) WHERE {where}")
    has_api = any(ROOT.glob("data/silver/india/equity_prices/bse/year=*/month=*/*.parquet"))
    has_q = any(ROOT.glob("data/quarantine/india/equity_prices/bse_bhavcopy/year=*/month=*/*.parquet"))

    api_cte = (f"""api AS (SELECT bse_scrip_code, strftime(trade_date, '%Y-%m') AS period,
                    count(DISTINCT trade_date) FILTER (WHERE delivery_quantity IS NOT NULL) AS delivery_days
                FROM read_parquet('{API}', union_by_name=true) GROUP BY 1, 2)"""
               if has_api else "api AS (SELECT NULL::VARCHAR bse_scrip_code, NULL::VARCHAR period, 0 delivery_days WHERE false)")
    q_cte = (f"""q AS (SELECT bse_scrip_code, strftime(trade_date, '%Y-%m') AS period, count(*) AS quarantined_rows
                FROM read_parquet('{QUAR}', union_by_name=true) GROUP BY 1, 2)"""
             if has_q else "q AS (SELECT NULL::VARCHAR bse_scrip_code, NULL::VARCHAR period, 0 quarantined_rows WHERE false)")

    ledger = con.execute(f"""
        WITH days AS (
            SELECT strftime(trade_date, '%Y-%m') AS period, count(DISTINCT trade_date) AS exchange_trading_days
            FROM bhav GROUP BY 1),
        s AS (
            SELECT bse_scrip_code, strftime(trade_date, '%Y-%m') AS period,
                   count(DISTINCT trade_date) AS days_with_data,
                   min(trade_date)::DATE AS first_trade_date, max(trade_date)::DATE AS last_trade_date,
                   arg_max(registry_status, trade_date) AS registry_status,
                   arg_max(instrument_id, trade_date) AS instrument_id,
                   arg_max(exchange_listing_id, trade_date) AS exchange_listing_id,
                   arg_max(company_name, trade_date) AS company_name,
                   arg_max(bse_symbol, trade_date) AS bse_symbol,
                   arg_max(isin, trade_date) AS isin,
                   arg_max(security_series, trade_date) AS security_series
            FROM bhav GROUP BY 1, 2),
        {api_cte},
        {q_cte}
        SELECT s.bse_scrip_code, s.period, days.exchange_trading_days, s.days_with_data,
               round(s.days_with_data / days.exchange_trading_days, 4) AS coverage_ratio,
               s.first_trade_date, s.last_trade_date, s.registry_status, s.instrument_id,
               s.exchange_listing_id, s.company_name, s.bse_symbol, s.isin, s.security_series,
               coalesce(q.quarantined_rows, 0) AS quarantined_rows,
               coalesce(api.delivery_days, 0) AS delivery_days,
               CASE WHEN coalesce(api.delivery_days, 0) = 0 THEN 'NOT_ENRICHED'
                    WHEN api.delivery_days >= s.days_with_data THEN 'ENRICHED'
                    ELSE 'PARTIAL' END AS delivery_status
        FROM s JOIN days USING (period)
        LEFT JOIN api USING (bse_scrip_code, period)
        LEFT JOIN q USING (bse_scrip_code, period)
        ORDER BY s.bse_scrip_code, s.period
    """).df()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    ledger.to_csv(OUTPUT, index=False)

    print("BSE BHAVCOPY COVERAGE LEDGER")
    print("Scrips:", ledger.bse_scrip_code.nunique(), "| months:", ledger.period.nunique(),
          "| scrip-months:", len(ledger))
    print("Period range:", ledger.period.min(), "->", ledger.period.max())
    print("\nREGISTRY_STATUS (scrip-months):")
    print(ledger.registry_status.value_counts().to_string())
    print("\nDistinct scrips by registry status:")
    print(ledger.groupby("registry_status").bse_scrip_code.nunique().to_string())
    print("\nDELIVERY_STATUS:")
    print(ledger.delivery_status.value_counts().to_string())
    print("\nScrip-months with coverage_ratio < 1 (non-trading days are normal for illiquid scrips):",
          int((ledger.coverage_ratio < 1).sum()))
    print("Quarantined rows:", int(ledger.quarantined_rows.sum()))
    print("Bhavcopy observations:", int(ledger.days_with_data.sum()))
    print("\nSaved:", OUTPUT)


if __name__ == "__main__":
    main()

import argparse
import json
import sqlite3
from pathlib import Path

import duckdb
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]

DOWNLOAD_LOG = ROOT / "data/staging/india/bse_historical_download_log.csv"
CANDIDATES = ROOT / "data/silver/reference/india/bse_daily_api_candidates.csv"
BRONZE = ROOT / "data/bronze/india/equity_prices/bse"
SILVER = ROOT / "data/silver/india/equity_prices/bse"
DB = ROOT / "data/registry/quantera_registry.db"
OUTPUT = ROOT / "data/silver/reference/india/bse_historical_coverage.csv"


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="YYYY-MM")
    parser.add_argument("--end", required=True, help="YYYY-MM")
    parser.add_argument("--codes", default=None, help="Comma-separated BSE codes")
    return parser.parse_args()


def read_bronze(path):
    if not path.exists():
        return "MISSING", 0

    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)

        if not isinstance(payload, dict) or "StockData" not in payload:
            return "INVALID", 0

        rows = payload["StockData"]

        if rows is None or rows == []:
            return "EMPTY", 0

        if not isinstance(rows, list):
            return "INVALID", 0

        if any(not isinstance(row, dict) for row in rows):
            return "INVALID", 0

        return "DOWNLOADED", len(rows)

    except Exception:
        return "INVALID", 0


def main():
    args = parse_args()

    start = pd.Period(args.start, freq="M")
    end = pd.Period(args.end, freq="M")

    if start > end:
        raise SystemExit("--start must not be later than --end")

    master = pd.read_csv(CANDIDATES, dtype=str)
    master["bse_scrip_code"] = master["bse_scrip_code"].str.strip()

    if args.codes:
        selected = {x.strip() for x in args.codes.split(",") if x.strip()}
        master = master[master["bse_scrip_code"].isin(selected)]

    if master.empty:
        raise SystemExit("No matching securities found.")

    months = pd.period_range(start, end, freq="M")

    grid = pd.MultiIndex.from_product(
        [master["bse_scrip_code"].tolist(), months],
        names=["bse_scrip_code", "period_obj"],
    ).to_frame(index=False)

    grid["year"] = grid["period_obj"].map(lambda x: x.year)
    grid["month"] = grid["period_obj"].map(lambda x: x.month)
    grid["period"] = grid["period_obj"].astype(str)
    grid = grid.drop(columns="period_obj")

    # Download attempt history
    if DOWNLOAD_LOG.exists():
        log = pd.read_csv(DOWNLOAD_LOG, dtype={"scrip_code": str})

        log["year"] = pd.to_numeric(log["year"], errors="coerce")
        log["month"] = pd.to_numeric(log["month"], errors="coerce")
        log = log.dropna(subset=["scrip_code", "year", "month"])

        log["year"] = log["year"].astype(int)
        log["month"] = log["month"].astype(int)
        log = log[
            log["scrip_code"].isin(master["bse_scrip_code"])
            & (log["year"] * 100 + log["month"]).between(
                start.year * 100 + start.month,
                end.year * 100 + end.month,
            )
        ]

        attempts = (
            log.groupby(["scrip_code", "year", "month"])
            .agg(
                total_attempts=("status", "size"),
                failed_attempts=(
                    "status", lambda x: (x == "FAILED").sum()
                ),
                last_log_status=("status", "last"),
                last_http_status=("http_status", "last"),
                last_details=("details", "last"),
            )
            .reset_index()
            .rename(columns={"scrip_code": "bse_scrip_code"})
        )

        grid = grid.merge(
            attempts,
            on=["bse_scrip_code", "year", "month"],
            how="left",
        )
    else:
        grid["total_attempts"] = 0
        grid["failed_attempts"] = 0
        grid["last_log_status"] = None
        grid["last_http_status"] = None
        grid["last_details"] = None

    grid["total_attempts"] = grid["total_attempts"].fillna(0).astype(int)
    grid["failed_attempts"] = grid["failed_attempts"].fillna(0).astype(int)

    # Inspect actual Bronze files
    bronze_statuses = []
    bronze_counts = []
    bronze_paths = []

    for row in grid.itertuples(index=False):
        code = row.bse_scrip_code
        year = row.year
        month = row.month

        path = (
            BRONZE / f"year={year}" / f"month={month:02d}"
            / f"bse_daily_{code}_{year}{month:02d}.json"
        )

        status, count = read_bronze(path)

        if status == "MISSING":
            if row.last_log_status == "FAILED":
                status = "FAILED"
            else:
                status = "PENDING"

        bronze_statuses.append(status)
        bronze_counts.append(count)
        bronze_paths.append(str(path) if path.exists() else "")

    grid["bronze_status"] = bronze_statuses
    grid["bronze_record_count"] = bronze_counts
    grid["bronze_file"] = bronze_paths

    # Inspect Silver files
    grid["silver_status"] = "NOT_PROCESSED"
    grid["silver_record_count"] = 0
    grid["first_trade_date"] = None
    grid["last_trade_date"] = None
    grid["silver_file"] = ""

    with duckdb.connect() as con:
        for year, month in months.to_series().map(
            lambda p: (p.year, p.month)
        ).drop_duplicates():
            mask = (grid["year"] == year) & (grid["month"] == month)
            codes = grid.loc[mask, "bse_scrip_code"].tolist()

            path = (
                SILVER / f"year={year}" / f"month={month:02d}"
                / f"bse_daily_{year}{month:02d}.parquet"
            )

            if not path.exists():
                continue

            placeholders = ",".join("?" for _ in codes)

            result = con.execute(
                f"""
                SELECT
                    bse_scrip_code,
                    COUNT(*) AS record_count,
                    MIN(trade_date) AS first_trade_date,
                    MAX(trade_date) AS last_trade_date
                FROM read_parquet(?)
                WHERE bse_scrip_code IN ({placeholders})
                GROUP BY bse_scrip_code
                """,
                [str(path)] + codes,
            ).df()

            lookup = {
                str(r.bse_scrip_code): r
                for r in result.itertuples(index=False)
            }

            for idx in grid.index[mask]:
                code = grid.at[idx, "bse_scrip_code"]
                grid.at[idx, "silver_file"] = str(path)

                if code in lookup:
                    item = lookup[code]
                    grid.at[idx, "silver_record_count"] = int(item.record_count)
                    grid.at[idx, "first_trade_date"] = item.first_trade_date
                    grid.at[idx, "last_trade_date"] = item.last_trade_date
                    grid.at[idx, "silver_status"] = "NORMALIZED"
                elif grid.at[idx, "bronze_status"] == "EMPTY":
                    grid.at[idx, "silver_status"] = "NO_RECORDS"

    # Registry linkage
    grid = grid.merge(
        master[
            [
                "bse_scrip_code",
                "bse_symbol",
                "company_name",
                "isin",
                "security_series",
                "security_status",
            ]
        ],
        on="bse_scrip_code",
        how="left",
    )

    grid["registry_status"] = "UNKNOWN"

    if DB.exists():
        with sqlite3.connect(DB) as conn:
            registry = pd.read_sql_query(
                """
                SELECT listing_code, instrument_id, exchange_listing_id
                FROM exchange_listings
                WHERE exchange_mic = 'XBOM'
                """,
                conn,
            )

        registry["listing_code"] = registry["listing_code"].astype(str)

        grid = grid.merge(
            registry,
            left_on="bse_scrip_code",
            right_on="listing_code",
            how="left",
        )

        grid["registry_status"] = grid["instrument_id"].apply(
            lambda x: "LINKED" if pd.notna(x) else "UNLINKED"
        )

    grid["updated_at_utc"] = pd.Timestamp.now(tz="UTC").isoformat()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    grid.to_csv(OUTPUT, index=False)

    print("\nBSE HISTORICAL COVERAGE LEDGER")
    print("Securities:", grid["bse_scrip_code"].nunique())
    print("Months:", len(months))
    print("Expected combinations:", len(grid))

    for column in ["bronze_status", "silver_status", "registry_status"]:
        print(f"\n{column.upper()}:")
        print(grid[column].value_counts(dropna=False).to_string())

    print("\nBronze observations:", grid["bronze_record_count"].sum())
    print("Silver observations:", grid["silver_record_count"].sum())
    print("Total attempts:", grid["total_attempts"].sum())
    print("Failed attempts:", grid["failed_attempts"].sum())
    print("\nSaved:", OUTPUT)


if __name__ == "__main__":
    main()

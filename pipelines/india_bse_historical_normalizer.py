import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data/bronze/india/equity_prices/bse"
SILVER = ROOT / "data/silver/india/equity_prices/bse"
MASTER = ROOT / "data/silver/reference/india/bse_security_master_v2.csv"
DB = ROOT / "data/registry/quantera_registry.db"

NUMERIC_FIELDS = {
    "qe_open": "open",
    "qe_high": "high",
    "qe_low": "low",
    "qe_close": "close",
    "WeightedPrice": "weighted_price",
    "no_of_shrs": "volume",
    "no_trades": "trade_count",
    "net_turnov": "turnover",
    "Delivery_Qty": "delivery_quantity",
    "Perc_Del_Qty": "delivery_percentage",
    "SpreadHighLow": "spread_high_low",
    "SpreadOpenClose": "spread_open_close",
}


def number(value):
    if value is None or str(value).strip() in ("", "-"):
        return None
    return float(str(value).replace(",", "").strip())


def load_registry():
    if not DB.exists():
        raise FileNotFoundError(f"Registry not found: {DB}")

    with sqlite3.connect(DB) as conn:
        return pd.read_sql_query(
            """
            SELECT
                exchange_listing_id,
                instrument_id,
                listing_code,
                listing_symbol,
                listing_name,
                security_series
            FROM exchange_listings
            WHERE exchange_mic = 'XBOM'
            """,
            conn,
        )


def normalize_file(path, master, registry):
    payload = json.loads(path.read_text(encoding="utf-8"))

    scrip_code = str(payload.get("scrip_cd", "")).strip()
    if not scrip_code:
        raise ValueError(f"Missing scrip_cd in {path}")

    records = payload.get("StockData") or []
    if not isinstance(records, list):
        raise ValueError(f"StockData is not a list in {path}")

    master_match = master.loc[
        master["bse_scrip_code"].astype(str).str.strip() == scrip_code
    ]

    registry_match = registry.loc[
        registry["listing_code"].astype(str).str.strip() == scrip_code
    ]

    metadata = {
        "bse_scrip_code": scrip_code,
        "company_name": payload.get("scripname"),
        "bse_symbol": payload.get("scrip_id"),
        "isin": (
            master_match.iloc[0].get("isin")
            if not master_match.empty else None
        ),
        "instrument_id": (
            registry_match.iloc[0].get("instrument_id")
            if not registry_match.empty else None
        ),
        "exchange_listing_id": (
            registry_match.iloc[0].get("exchange_listing_id")
            if not registry_match.empty else None
        ),
        "security_series": (
            master_match.iloc[0].get("security_series")
            if not master_match.empty else None
        ),
    }

    rows = []

    for source_row_number, record in enumerate(records, start=1):
        trade_date = pd.to_datetime(
            record.get('RealDate'), errors='coerce'
        )
        if pd.isna(trade_date):
            raise ValueError(f'Invalid RealDate in {path}: {record.get("RealDate")}')

        observation_id = hashlib.sha256(
            f'{scrip_code}|{trade_date.isoformat()}|'
            f'{record.get("dematflagExist") or ""}|{source_row_number}'.encode()
        ).hexdigest()

        row = {
            **metadata,
            'source_row_number': source_row_number,
            'observation_id': observation_id,
            "trade_date": pd.to_datetime(
                record.get("RealDate"), errors="coerce"
            ),
            "source_date_label": record.get("Dates"),
            "demat_flag": record.get("dematflagExist"),
            "source_flag": record.get("strflag"),
            "source_description": record.get("strdmet"),
        }

        for source, target in NUMERIC_FIELDS.items():
            row[target] = number(record.get(source))

        rows.append(row)

    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", help="Start date YYYY-MM")
    parser.add_argument("--end", help="End date YYYY-MM")
    parser.add_argument("--codes", help="Comma-separated BSE scrip codes")
    args = parser.parse_args()

    if not MASTER.exists():
        raise FileNotFoundError(f"BSE master not found: {MASTER}")

    master = pd.read_csv(MASTER, dtype=str).fillna("")
    registry = load_registry()

    files = sorted(BRONZE.rglob("bse_daily_*.json"))

    start_period = args.start if args.start else "0000-01"
    end_period = args.end if args.end else "9999-12"

    for value in (start_period, end_period):
        try:
            parsed = pd.Period(value, freq="M")
        except Exception:
            raise SystemExit(f"Invalid month format: {value}. Expected YYYY-MM")

    if start_period > end_period:
        raise SystemExit("--start must not be after --end")

    selected_files = []

    for path in files:
        try:
            year = int(path.parent.parent.name.replace("year=", ""))
            month = int(path.parent.name.replace("month=", ""))
            period = f"{year:04d}-{month:02d}"

            if start_period <= period <= end_period:
                selected_files.append(path)
        except (ValueError, IndexError):
            print(f"SKIPPING INVALID BRONZE PATH: {path}")

    files = selected_files

    if args.codes:
        codes = {x.strip() for x in args.codes.split(",")}
        files = [
            p for p in files
            if p.name.split("_")[2] in codes
        ]

    all_rows = []
    errors = []

    for path in files:
        try:
            rows = normalize_file(path, master, registry)
            all_rows.extend(rows)
            print(f"READ {path.name}: {len(rows)} records")
        except Exception as exc:
            errors.append((str(path), str(exc)))
            print(f"ERROR {path}: {exc}")

    if not all_rows:
        print("No daily records found.")

        if errors:
            raise SystemExit(
                f"Normalization failed: {len(errors)} Bronze file errors."
            )

        return

    df = pd.DataFrame(all_rows)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date

    duplicate_count = df.duplicated(
        subset=["observation_id"]
    ).sum()

    invalid_ohlc = (
        (df["high"] < df["low"])
        | (df["high"] < df["open"])
        | (df["high"] < df["close"])
        | (df["low"] > df["open"])
        | (df["low"] > df["close"])
    ).fillna(False).sum()

    if duplicate_count:
        raise ValueError(f"Duplicate scrip/date records: {duplicate_count}")

    if invalid_ohlc:
        raise ValueError(f"Invalid OHLC records: {invalid_ohlc}")

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["volume"] = pd.array(df["volume"], dtype="Int64")
    df["trade_count"] = pd.array(df["trade_count"], dtype="Int64")
    df["delivery_quantity"] = pd.array(df["delivery_quantity"], dtype="Float64")
    df["delivery_percentage"] = pd.array(df["delivery_percentage"], dtype="Float64")

    df["year"] = df["trade_date"].dt.year
    df["month"] = df["trade_date"].dt.month

    for (year, month), group in df.groupby(["year", "month"]):
        output_dir = SILVER / f"year={year}" / f"month={month:02d}"
        output_dir.mkdir(parents=True, exist_ok=True)

        output = output_dir / f"bse_daily_{year}{month:02d}.parquet"

        table = pa.Table.from_pandas(
            group.drop(columns=["year", "month"]),
            schema=pa.schema([
                ("bse_scrip_code", pa.string()),
                ("company_name", pa.string()),
                ("bse_symbol", pa.string()),
                ("isin", pa.string()),
                ("instrument_id", pa.string()),
                ("exchange_listing_id", pa.string()),
                ("security_series", pa.string()),
                ("trade_date", pa.timestamp("ns")),
                ("source_date_label", pa.string()),
                ("demat_flag", pa.string()),
                ("source_row_number", pa.int64()),
                ("observation_id", pa.string()),
                ("source_flag", pa.string()),
                ("source_description", pa.string()),
                ("open", pa.float64()),
                ("high", pa.float64()),
                ("low", pa.float64()),
                ("close", pa.float64()),
                ("weighted_price", pa.float64()),
                ("volume", pa.int64()),
                ("trade_count", pa.int64()),
                ("turnover", pa.float64()),
                ("delivery_quantity", pa.float64()),
                ("delivery_percentage", pa.float64()),
                ("spread_high_low", pa.float64()),
                ("spread_open_close", pa.float64()),
            ]),
            preserve_index=False,
        )
        # Merge existing monthly Silver data before writing.
        if output.exists():
            existing_df = pq.read_table(output).to_pandas()
            incoming_df = table.to_pandas()

            combined_df = pd.concat(
                [existing_df, incoming_df],
                ignore_index=True,
            )

            combined_df["trade_date"] = pd.to_datetime(
                combined_df["trade_date"]
            )

            before_dedup = len(combined_df)

            combined_df = combined_df.drop_duplicates(
                subset=["observation_id"],
                keep="last",
            )

            merged_duplicates = before_dedup - len(combined_df)

            table = pa.Table.from_pandas(
                combined_df,
                schema=table.schema,
                preserve_index=False,
                safe=False,
            )

            print(
                f"MERGED existing data: {len(existing_df)} records; "
                f"removed duplicates: {merged_duplicates}"
            )

        temp_output = output.with_name(output.stem + ".tmp.parquet")

        try:
            pq.write_table(
                table,
                temp_output,
                compression="zstd",
            )
            temp_output.replace(output)
        finally:
            if temp_output.exists():
                temp_output.unlink()

        print(f"WRITTEN {output}: {table.num_rows} records")

    print("\nBSE NORMALIZATION SUMMARY")
    print("Bronze files:", len(files))
    print("Silver records:", len(df))
    print("Unique scrip codes:", df["bse_scrip_code"].nunique())
    print("Date range:", df["trade_date"].min(), "to", df["trade_date"].max())
    print("Unlinked instrument records:", df["instrument_id"].isna().sum())
    print("Duplicate records:", duplicate_count)
    print("Invalid OHLC records:", invalid_ohlc)
    print("File errors:", len(errors))

    if errors:
        raise SystemExit(
            f"Normalization completed with {len(errors)} file errors."
        )


if __name__ == "__main__":
    main()

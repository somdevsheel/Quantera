import sys
import zipfile
from pathlib import Path
import pandas as pd

BRONZE = Path("data/bronze/india/equity_prices")
SILVER = Path("data/silver/india/equity_prices")
BRONZE.mkdir(parents=True, exist_ok=True)
SILVER.mkdir(parents=True, exist_ok=True)

if len(sys.argv) < 2:
    raise SystemExit(
        "Usage: python pipelines/india_nse_bhavcopy_batch.py <zip1> [zip2 ...]"
    )

numeric_columns = {
    "OpnPric": "open",
    "HghPric": "high",
    "LwPric": "low",
    "ClsPric": "close",
    "LastPric": "last_price",
    "PrvsClsgPric": "previous_close",
    "TtlTradgVol": "volume",
    "TtlTrfVal": "traded_value",
    "TtlNbOfTxsExctd": "trade_count",
}

for source_arg in sys.argv[1:]:
    source = Path(source_arg)

    if not source.exists():
        print(f"SKIPPED: Missing file {source}")
        continue

    with zipfile.ZipFile(source) as archive:
        csv_files = [n for n in archive.namelist()
                     if n.lower().endswith(".csv")]
        if len(csv_files) != 1:
            print(f"SKIPPED: Expected one CSV in {source}")
            continue

        with archive.open(csv_files[0]) as f:
            raw = pd.read_csv(f, dtype={"ISIN": str, "TckrSymb": str})

    raw.columns = raw.columns.str.strip()

    if "SctyTp" in raw.columns:
        df = raw[
            (raw["FinInstrmTp"] == "STK") &
            (raw["SctyTp"] == "EQ")
        ].copy()
    else:
        df = raw[
            (raw["FinInstrmTp"] == "STK") &
            (raw["SctySrs"].isin(["EQ", "BE", "BZ", "SM", "ST"]))
        ].copy()

    result = pd.DataFrame({
        "trade_date": pd.to_datetime(df["TradDt"], errors="coerce"),
        "isin": df["ISIN"].astype("string").str.strip(),
        "symbol": df["TckrSymb"].astype("string").str.strip(),
        "security_series": df["SctySrs"].astype("string").str.strip(),
        "exchange": "NSE",
        "exchange_mic": "XNSE",
        "currency": "INR",
    })

    for source_col, target_col in numeric_columns.items():
        result[target_col] = pd.to_numeric(
            df[source_col], errors="coerce"
        ).values

    result["instrument_id"] = "IN:" + result["isin"].fillna("")
    result.loc[
        result["isin"].isna() | result["isin"].eq(""),
        "instrument_id"
    ] = None

    valid = (
        result["trade_date"].notna() &
        result["symbol"].notna() &
        result["symbol"].ne("") &
        result["close"].gt(0) &
        result["high"].ge(result["low"]) &
        result["high"].ge(result["open"]) &
        result["low"].le(result["open"])
    )

    invalid = result[~valid].copy()
    result = result[valid].copy()

    result["volume"] = result["volume"].fillna(0).astype("int64")
    result["trade_count"] = result["trade_count"].fillna(0).astype("int64")

    result = result.drop_duplicates(
        ["trade_date", "exchange_mic", "symbol"], keep="last"
    )

    if result.empty:
        print(f"SKIPPED: No valid price rows in {source}")
        continue

    dates = result["trade_date"].dt.strftime("%Y%m%d").unique()

    if len(dates) != 1:
        print(f"SKIPPED: Multiple trading dates in {source}: {dates}")
        continue

    trade_date = dates[0]
    bronze_path = BRONZE / f"nse_bhavcopy_{trade_date}.csv"
    silver_path = SILVER / f"nse_equity_prices_{trade_date}.parquet"
    quarantine_path = SILVER / f"nse_invalid_records_{trade_date}.csv"

    if silver_path.exists():
        print(f"SKIPPED: Already normalized {trade_date}")
        continue

    with zipfile.ZipFile(source) as archive:
        with archive.open(csv_files[0]) as f:
            raw.to_csv(bronze_path, index=False)

    result.to_parquet(silver_path, index=False)
    invalid.to_csv(quarantine_path, index=False)

    print(
        f"{trade_date}: raw={len(raw)}, candidates={len(df)}, "
        f"valid={len(result)}, invalid={len(invalid)}"
    )
    print(f"  Bronze: {bronze_path}")
    print(f"  Silver: {silver_path}")
    print(f"  Quarantine: {quarantine_path}")

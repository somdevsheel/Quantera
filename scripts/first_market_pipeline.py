
from datetime import date, timedelta
from pathlib import Path

import boto3
import duckdb
import polars as pl

BUCKET = "quant-global-data-lake-prod"
REGION = "ap-southeast-2"

LOCAL_DIR = Path("data/staging")
LOCAL_DIR.mkdir(parents=True, exist_ok=True)

LOCAL_FILE = LOCAL_DIR / "sample_daily_bars.parquet"
S3_KEY = "bronze/market/sample_daily_bars.parquet"


def create_sample_data():
    dates = [date(2026, 10, 1) + timedelta(days=i) for i in range(10)]

    df = pl.DataFrame({
        "instrument_id": ["SAMPLE-001"] * len(dates),
        "symbol": ["DEMO"] * len(dates),
        "exchange_mic": ["XNYS"] * len(dates),
        "trading_date": dates,
        "open": [100.0, 101.0, 102.0, 101.5, 103.0,
                 104.0, 103.5, 105.0, 106.0, 107.0],
        "high": [102.0, 103.0, 104.0, 103.0, 105.0,
                 106.0, 105.0, 107.0, 108.0, 109.0],
        "low": [99.0, 100.0, 101.0, 100.5, 102.0,
                103.0, 102.5, 104.0, 105.0, 106.0],
        "close": [101.0, 102.0, 103.0, 102.0, 104.0,
                  105.0, 104.0, 106.0, 107.0, 108.0],
        "volume": [10000, 12000, 15000, 11000, 18000,
                   20000, 17000, 22000, 25000, 30000],
        "currency": ["USD"] * len(dates),
        "source": ["SIMULATED"] * len(dates),
    })

    return df


def validate_data(df):
    if df.is_empty():
        raise ValueError("Dataset is empty")

    invalid = df.filter(
        (pl.col("high") < pl.max_horizontal("open", "close"))
        | (pl.col("low") > pl.min_horizontal("open", "close"))
        | (pl.col("volume") < 0)
        | (pl.col("high") < pl.col("low"))
    )

    if invalid.height:
        raise ValueError(f"Invalid OHLCV records: {invalid.height}")

    duplicates = (
        df.group_by(["instrument_id", "trading_date"])
        .len()
        .filter(pl.col("len") > 1)
    )

    if duplicates.height:
        raise ValueError("Duplicate instrument/date records found")

    print(f"Validation passed: {df.height} records")
    return df


def save_parquet(df):
    df.write_parquet(LOCAL_FILE, compression="zstd")
    print(f"Parquet created: {LOCAL_FILE}")


def upload_to_s3():
    s3 = boto3.client("s3", region_name=REGION)

    s3.upload_file(
        str(LOCAL_FILE),
        BUCKET,
        S3_KEY,
        ExtraArgs={"ContentType": "application/vnd.apache.parquet"},
    )

    print(f"Uploaded to s3://{BUCKET}/{S3_KEY}")


def verify_with_duckdb():
    con = duckdb.connect()

    result = con.execute(
        "SELECT COUNT(*) AS records, "
        "MIN(trading_date) AS first_date, "
        "MAX(trading_date) AS last_date "
        "FROM read_parquet(?)",
        [str(LOCAL_FILE)],
    ).fetchone()

    print("DuckDB verification:")
    print(f"Records: {result[0]}")
    print(f"First date: {result[1]}")
    print(f"Last date: {result[2]}")

    con.close()


def main():
    df = create_sample_data()
    df = validate_data(df)
    save_parquet(df)
    upload_to_s3()
    verify_with_duckdb()

    print("\nQuantera first data pipeline completed!")


if __name__ == "__main__":
    main()

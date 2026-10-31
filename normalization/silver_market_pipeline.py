import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timezone

import boto3
import polars as pl


# Configuration
DATA_DIR = Path("data/staging/market")
OUTPUT_DIR = Path("data/silver/market")

DB_PATH = Path("data/registry/quantera_registry.db")

BUCKET = "quant-global-data-lake-prod"
REGION = "ap-southeast-2"

PROVIDER = "yahoo_finance"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("Quantera.Silver")


s3 = boto3.client("s3", region_name=REGION)


def get_instrument_mapping():
    connection = sqlite3.connect(DB_PATH)

    rows = connection.execute("""
        SELECT
            i.instrument_id,
            i.canonical_symbol,
            p.provider_symbol
        FROM instruments i
        JOIN provider_symbols p
            ON i.instrument_id = p.instrument_id
        WHERE p.provider = ?
    """, (PROVIDER,)).fetchall()

    connection.close()

    return {
        row[2]: {
            "instrument_id": row[0],
            "canonical_symbol": row[1],
        }
        for row in rows
    }


def transform_file(file_path, mapping):

    df = pl.read_parquet(file_path)

    provider_symbol = str(df["symbol"][0])

    if provider_symbol not in mapping:
        raise ValueError(
            f"No instrument mapping found for {provider_symbol}"
        )

    instrument = mapping[provider_symbol]

    # Standardize column names.
    df = df.rename({
        column: column.lower()
        for column in df.columns
    })

    # Convert trading date to Date type.
    df = df.with_columns(
        pl.col("trading_date").cast(pl.Date)
    )

    # Standardize numerical columns.
    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "adj_close",
    ]

    expressions = []

    for column in numeric_columns:
        if column in df.columns:
            expressions.append(
                pl.col(column).cast(pl.Float64, strict=False)
            )

    if expressions:
        df = df.with_columns(expressions)

    # Attach permanent instrument identity.
    df = df.with_columns([
        pl.lit(instrument["instrument_id"]).alias("instrument_id"),
        pl.lit(instrument["canonical_symbol"]).alias(
            "canonical_symbol"
        ),
        pl.lit(PROVIDER).alias("data_provider"),
        pl.lit("DAILY").alias("frequency"),
        pl.lit(
            datetime.now(timezone.utc).isoformat()
        ).alias("silver_processed_at"),
        pl.lit(
            "UNADJUSTED_OHLC_WITH_ADJ_CLOSE"
        ).alias("price_adjustment_basis"),
    ])

    # Preserve the original provider columns and values.
    # Sort into chronological order.
    df = df.sort("trading_date")

    return df


def upload_to_s3(local_file, s3_key):

    s3.upload_file(
        str(local_file),
        BUCKET,
        s3_key,
    )

    logger.info("Uploaded: s3://%s/%s", BUCKET, s3_key)


def run_pipeline():

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    mapping = get_instrument_mapping()

    if not mapping:
        raise RuntimeError("Instrument mapping is empty.")

    files = sorted(DATA_DIR.glob("*.parquet"))

    if not files:
        raise RuntimeError("No input Parquet files found.")

    successful = 0
    failed = 0

    for file_path in files:

        try:
            logger.info("Processing %s", file_path.name)

            df = transform_file(file_path, mapping)

            canonical_symbol = str(df["canonical_symbol"][0])

            instrument_id = str(df["instrument_id"][0])

            output_file = (
                OUTPUT_DIR
                / f"symbol={canonical_symbol}"
                / "daily_bars.parquet"
            )

            output_file.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            df.write_parquet(
                output_file,
                compression="zstd",
                statistics=True,
            )

            s3_key = (
                "silver/daily-bars/"
                f"symbol={canonical_symbol}/"
                f"daily_bars.parquet"
            )

            upload_to_s3(output_file, s3_key)

            logger.info(
                "SUCCESS | %s | %s records | Instrument ID: %s",
                canonical_symbol,
                df.height,
                instrument_id,
            )

            successful += 1

        except Exception as error:

            logger.exception(
                "FAILED: %s: %s",
                file_path.name,
                error,
            )

            failed += 1

    print("\nQuantera Silver Pipeline Summary")
    print("--------------------------------")
    print(f"Files processed: {successful + failed}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    run_pipeline()

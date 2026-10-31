from pathlib import Path
from datetime import datetime, timezone
import logging
import sqlite3

import polars as pl
import boto3


INPUT_DIR = Path("data/staging/market")
OUTPUT_DIR = Path("data/gold/adjusted-prices")

DB_PATH = Path("data/registry/quantera_registry.db")

BUCKET = "quant-global-data-lake-prod"
REGION = "ap-southeast-2"

PROVIDER = "yahoo_finance"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("Quantera.CorporateActions")

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


def adjust_prices(file_path, mapping):

    df = pl.read_parquet(file_path).sort("trading_date")

    provider_symbol = str(df["symbol"][0])

    if provider_symbol not in mapping:
        raise ValueError(f"Unknown instrument: {provider_symbol}")

    instrument = mapping[provider_symbol]

    required = [
        "open", "high", "low", "close",
        "adj_close", "dividends", "stock_splits",
    ]

    missing = [column for column in required if column not in df.columns]

    if missing:
        raise ValueError(f"Missing columns: {missing}")

    # Calculate adjustment factor from Yahoo adjusted close.
    df = df.with_columns(
        pl.when(
            pl.col("close").is_not_null()
            & (pl.col("close") != 0)
        )
        .then(pl.col("adj_close") / pl.col("close"))
        .otherwise(None)
        .alias("adjustment_factor")
    )

    # Apply the same factor to all OHLC prices.
    df = df.with_columns([
        (pl.col("open") * pl.col("adjustment_factor"))
            .alias("adjusted_open"),

        (pl.col("high") * pl.col("adjustment_factor"))
            .alias("adjusted_high"),

        (pl.col("low") * pl.col("adjustment_factor"))
            .alias("adjusted_low"),

        pl.col("adj_close").alias("adjusted_close"),
    ])

    # Add permanent instrument identity and metadata.
    df = df.with_columns([
        pl.lit(instrument["instrument_id"]).alias("instrument_id"),
        pl.lit(instrument["canonical_symbol"]).alias("canonical_symbol"),
        pl.lit(PROVIDER).alias("data_provider"),
        pl.lit("YAHOO_ADJ_CLOSE_FACTOR").alias(
            "adjustment_method"
        ),
        pl.lit(
            datetime.now(timezone.utc).isoformat()
        ).alias("processed_at"),
    ])

    return df


def process_instrument(file_path, mapping):

    df = adjust_prices(file_path, mapping)

    symbol = str(df["canonical_symbol"][0])

    output_file = (
        OUTPUT_DIR
        / f"symbol={symbol}"
        / "adjusted_prices.parquet"
    )

    output_file.parent.mkdir(parents=True, exist_ok=True)

    df.write_parquet(
        output_file,
        compression="zstd",
        statistics=True,
    )

    s3_key = (
        f"gold/adjusted-prices/"
        f"symbol={symbol}/adjusted_prices.parquet"
    )

    s3.upload_file(
        str(output_file),
        BUCKET,
        s3_key,
    )

    logger.info(
        "SUCCESS | %s | %s records | S3: %s",
        symbol,
        df.height,
        s3_key,
    )


def main():

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    mapping = get_instrument_mapping()

    files = sorted(INPUT_DIR.glob("*.parquet"))

    successful = 0
    failed = 0

    for file_path in files:

        try:
            logger.info("Processing %s", file_path.name)

            process_instrument(file_path, mapping)

            successful += 1

        except Exception:
            logger.exception("Failed: %s", file_path.name)
            failed += 1

    print("\nQuantera Corporate Action Engine")
    print("--------------------------------")
    print(f"Processed: {successful + failed}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    main()

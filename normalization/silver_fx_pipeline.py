
from pathlib import Path
from datetime import datetime, timezone

import polars as pl
import boto3
import logging

# Configuration
INPUT_FILE = Path("data/staging/fx/USDINR.parquet")
OUTPUT_DIR = Path("data/silver/fx")

BUCKET = "quant-global-data-lake-prod"
REGION = "ap-southeast-2"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("QuanteraSilverFX")

s3 = boto3.client("s3", region_name=REGION)


def load_fx_data():

    if not INPUT_FILE.exists():
        raise FileNotFoundError(INPUT_FILE)

    return pl.read_parquet(INPUT_FILE).sort("trading_date")


def normalize_fx_data(df):

    df = df.with_columns([
        pl.col("close").alias("usd_inr_rate"),
        pl.col("open").alias("usd_inr_open"),
        pl.col("high").alias("usd_inr_high"),
        pl.col("low").alias("usd_inr_low"),

        (
            (pl.col("high") < pl.col("open")) |
            (pl.col("high") < pl.col("close")) |
            (pl.col("low") > pl.col("open")) |
            (pl.col("low") > pl.col("close"))
        ).alias("ohlc_quality_flag"),

        pl.lit("DAILY").alias("frequency"),
        pl.lit("USD").alias("base_currency"),
        pl.lit("INR").alias("quote_currency"),
        pl.lit("yahoo_finance").alias("data_provider"),
        pl.lit("PROVIDER_DAILY_CLOSE").alias("rate_type"),
        pl.lit(
            datetime.now(timezone.utc).isoformat()
        ).alias("silver_processed_at")
    ])

    df = df.with_columns([
        pl.col("usd_inr_rate")
        .pct_change()
        .alias("daily_return"),

        pl.col("usd_inr_rate")
        .log()
        .diff()
        .alias("daily_log_return"),

        pl.col("usd_inr_rate")
        .shift(1)
        .alias("previous_available_rate"),

        pl.col("trading_date")
        .shift(1)
        .alias("previous_available_date")
    ])

    df = df.with_columns(
        (
            pl.col("usd_inr_rate") /
            pl.col("previous_available_rate")
        ).alias("fx_conversion_factor")
    )

    return df


def validate_fx_data(df):

    if df.height == 0:
        raise ValueError("Silver FX dataset is empty.")

    if df["trading_date"].n_unique() != df.height:
        raise ValueError("Duplicate FX dates found.")

    if df.filter(
        pl.col("usd_inr_rate").is_null() |
        (pl.col("usd_inr_rate") <= 0)
    ).height:
        raise ValueError("Invalid normalized FX rates.")

    if df.filter(
        pl.col("daily_return").is_not_null() &
        ~pl.col("daily_return").is_finite()
    ).height:
        raise ValueError("Non-finite FX returns found.")

    logger.info(
        "Silver FX validation passed: %s records",
        df.height
    )


def save_and_upload(df):

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    local_path = OUTPUT_DIR / "USDINR.parquet"

    df.write_parquet(
        local_path,
        compression="zstd"
    )

    s3_key = "silver/fx/pair=USDINR/daily_fx.parquet"

    s3.upload_file(
        str(local_path),
        BUCKET,
        s3_key
    )

    logger.info(
        "Uploaded to s3://%s/%s",
        BUCKET,
        s3_key
    )


def main():

    logger.info("Starting Silver FX pipeline...")

    raw_df = load_fx_data()

    silver_df = normalize_fx_data(raw_df)

    validate_fx_data(silver_df)

    save_and_upload(silver_df)

    print("\nQUANTERA SILVER FX PIPELINE")
    print("----------------------------")
    print(f"Records: {silver_df.height}")
    print(f"Start date: {silver_df['trading_date'].min()}")
    print(f"End date: {silver_df['trading_date'].max()}")
    print(
        "OHLC review flags:",
        silver_df["ohlc_quality_flag"].sum()
    )
    print("Status: SUCCESS")


if __name__ == "__main__":
    main()

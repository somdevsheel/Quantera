
from pathlib import Path
from datetime import date, datetime, timezone, timedelta

import yfinance as yf
import polars as pl
import boto3
import logging

# Configuration
SYMBOL = "USDINR=X"
PAIR = "USDINR"
START_DATE = "2006-01-01"

BUCKET = "quant-global-data-lake-prod"
REGION = "ap-southeast-2"

OUTPUT_DIR = Path("data/staging/fx")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("QuanteraFXIngestion")

s3 = boto3.client("s3", region_name=REGION)


def download_fx_data():

    logger.info("Downloading USD/INR historical data...")

    ticker = yf.Ticker(SYMBOL)

    history = ticker.history(
        start=START_DATE,
        end=date.today() + timedelta(days=1),
        interval="1d",
        auto_adjust=False,
        actions=False
    )

    if history.empty:
        raise RuntimeError("No FX data received from Yahoo Finance.")

    history = history.reset_index()

    history.columns = [
        str(column).lower().replace(" ", "_")
        for column in history.columns
    ]

    if "date" not in history.columns:
        if "datetime" in history.columns:
            history = history.rename({"datetime": "date"})
        else:
            raise ValueError("FX date column not found.")

    required = ["date", "open", "high", "low", "close"]

    missing = [
        column for column in required
        if column not in history.columns
    ]

    if missing:
        raise ValueError(f"Missing columns: {missing}")

    df = pl.from_pandas(history)

    df = df.with_columns(
        pl.col("date")
        .cast(pl.Datetime, strict=False)
        .dt.date()
        .alias("trading_date"),

        pl.lit(PAIR).alias("currency_pair"),
        pl.lit("INR").alias("quote_currency"),
        pl.lit("USD").alias("base_currency"),
        pl.lit("yahoo_finance").alias("provider"),
        pl.lit(datetime.now(timezone.utc).isoformat())
        .alias("ingested_at")
    )

    if "volume" not in df.columns:
        df = df.with_columns(
            pl.lit(0).alias("volume")
        )

    df = df.select([
        "trading_date",
        "currency_pair",
        "base_currency",
        "quote_currency",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "provider",
        "ingested_at"
    ])

    df = df.sort("trading_date")

    return df


def validate_fx_data(df):

    if df.height == 0:
        raise ValueError("FX dataset is empty.")

    if df["trading_date"].n_unique() != df.height:
        raise ValueError("Duplicate FX trading dates detected.")

    # Critical validation: prices must be positive
    invalid_prices = df.filter(
        (pl.col("open") <= 0) |
        (pl.col("high") <= 0) |
        (pl.col("low") <= 0) |
        (pl.col("close") <= 0)
    )

    if invalid_prices.height > 0:
        raise ValueError(
            f"Invalid non-positive prices: {invalid_prices.height}"
        )

    # Check OHLC consistency without modifying source data
    inconsistent_ohlc = df.filter(
        (pl.col("high") < pl.col("low")) |
        (pl.col("high") < pl.col("open")) |
        (pl.col("high") < pl.col("close")) |
        (pl.col("low") > pl.col("open")) |
        (pl.col("low") > pl.col("close"))
    )

    logger.warning(
        "OHLC consistency review required: %s records",
        inconsistent_ohlc.height
    )

    logger.info(
        "FX price validation passed: %s records",
        df.height
    )

    logger.info(
        "FX OHLC review records: %s",
        inconsistent_ohlc.height
    )



def save_and_upload(df):

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    local_path = OUTPUT_DIR / "USDINR.parquet"

    df.write_parquet(
        local_path,
        compression="zstd"
    )

    ingestion_date = date.today().isoformat()

    s3_key = (
        f"bronze/fx/provider=yahoo_finance/"
        f"pair=USDINR/ingestion_date={ingestion_date}/"
        f"daily_fx.parquet"
    )

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

    return local_path


def main():

    df = download_fx_data()

    validate_fx_data(df)

    local_path = save_and_upload(df)

    print("\nQUANTERA FX INGESTION")
    print("----------------------")
    print(f"Currency pair: {PAIR}")
    print(f"Records: {df.height}")
    print(f"Start date: {df['trading_date'].min()}")
    print(f"End date: {df['trading_date'].max()}")
    print(f"Local file: {local_path}")
    print("Status: SUCCESS")


if __name__ == "__main__":
    main()

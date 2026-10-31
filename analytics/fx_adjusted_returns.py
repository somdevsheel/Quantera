
from pathlib import Path
from datetime import datetime, timezone

import polars as pl
import boto3
import logging

# Configuration
INPUT_DIR = Path("data/gold/returns")
FX_FILE = Path("data/silver/fx/USDINR.parquet")
OUTPUT_DIR = Path("data/gold/fx-adjusted-returns")

BUCKET = "quant-global-data-lake-prod"
REGION = "ap-southeast-2"

US_SYMBOLS = ["AAPL", "GOOGL", "MSFT", "NVDA"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("QuanteraFXAdjustedReturns")

s3 = boto3.client("s3", region_name=REGION)


def load_fx_data():

    if not FX_FILE.exists():
        raise FileNotFoundError(FX_FILE)

    fx = pl.read_parquet(FX_FILE)

    fx = fx.select([
        "trading_date",
        "usd_inr_rate"
    ]).sort("trading_date")

    fx = fx.filter(
        pl.col("usd_inr_rate").is_not_null() &
        (pl.col("usd_inr_rate") > 0)
    )

    if fx.height == 0:
        raise ValueError("No valid FX observations.")

    return fx


def process_instrument(symbol, fx):

    input_file = (
        INPUT_DIR /
        f"symbol={symbol}" /
        "daily_returns.parquet"
    )

    if not input_file.exists():
        raise FileNotFoundError(input_file)

    equity = pl.read_parquet(input_file).sort("trading_date")

    equity = equity.with_columns(
        pl.col("trading_date").alias("equity_trading_date")
    )

    # Match each equity session to the latest available FX close
    result = equity.join_asof(
        fx,
        left_on="trading_date",
        right_on="trading_date",
        strategy="backward"
    )

    result = result.rename({
        "usd_inr_rate": "usd_inr_rate"
    })

    # Calculate FX movement between consecutive equity sessions
    result = result.with_columns([
        pl.col("usd_inr_rate")
        .shift(1)
        .alias("previous_equity_session_fx_rate"),

        pl.col("trading_date")
        .shift(1)
        .alias("previous_equity_session_date")
    ])

    result = result.with_columns([
        (
            pl.col("usd_inr_rate") /
            pl.col("previous_equity_session_fx_rate")
            - 1
        ).alias("fx_return"),

        (
            (1 + pl.col("daily_return")) *
            (
                pl.col("usd_inr_rate") /
                pl.col("previous_equity_session_fx_rate")
            ) - 1
        ).alias("daily_return_inr")
    ])

    result = result.with_columns([
        (
            (1 + pl.col("daily_return_inr")).log()
        ).alias("daily_log_return_inr"),

        pl.lit("USD_TO_INR").alias("conversion_pair"),

        pl.lit("ASOF_BACKWARD").alias("fx_alignment_method"),

        pl.lit("YAHOO_FINANCE_DAILY_CLOSE").alias("fx_rate_source"),

        pl.lit(
            datetime.now(timezone.utc).isoformat()
        ).alias("fx_processed_at")
    ])

    # Validate conversion output
    invalid = result.filter(
        pl.col("usd_inr_rate").is_null() |
        (pl.col("usd_inr_rate") <= 0)
    )

    if invalid.height > 0:
        raise ValueError(
            f"{symbol}: {invalid.height} records lack valid FX rates."
        )

    invalid_returns = result.filter(
        pl.col("daily_return_inr").is_not_null() &
        ~pl.col("daily_return_inr").is_finite()
    )

    if invalid_returns.height > 0:
        raise ValueError(
            f"{symbol}: invalid INR returns detected."
        )

    return result


def save_and_upload(df, symbol):

    output_dir = OUTPUT_DIR / f"symbol={symbol}"
    output_dir.mkdir(parents=True, exist_ok=True)

    local_file = output_dir / "daily_returns_inr.parquet"

    df.write_parquet(
        local_file,
        compression="zstd"
    )

    s3_key = (
        f"gold/fx-adjusted-returns/"
        f"currency=INR/symbol={symbol}/"
        f"daily_returns.parquet"
    )

    s3.upload_file(
        str(local_file),
        BUCKET,
        s3_key
    )

    logger.info(
        "Uploaded %s to s3://%s/%s",
        symbol,
        BUCKET,
        s3_key
    )


def main():

    logger.info("Loading historical USD/INR data...")

    fx = load_fx_data()

    successful = 0
    failed = 0

    for symbol in US_SYMBOLS:

        try:
            logger.info("Processing %s", symbol)

            result = process_instrument(symbol, fx)

            save_and_upload(result, symbol)

            print(
                f"{symbol}: {result.height} records | "
                f"INR returns generated"
            )

            successful += 1

        except Exception:
            logger.exception("Failed: %s", symbol)
            failed += 1

    print("\nQUANTERA INR RETURN ENGINE")
    print("---------------------------")
    print(f"Instruments: {successful + failed}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    main()


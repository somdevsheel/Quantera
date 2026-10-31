from pathlib import Path
from datetime import datetime, timezone
import logging

import polars as pl
import boto3


# Configuration
INPUT_DIR = Path("data/gold/adjusted-prices")
OUTPUT_DIR = Path("data/gold/returns")

BUCKET = "quant-global-data-lake-prod"
REGION = "ap-southeast-2"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("Quantera.Returns")

s3 = boto3.client("s3", region_name=REGION)


def calculate_returns(file_path):

    df = pl.read_parquet(file_path).sort("trading_date")

    required_columns = [
        "instrument_id",
        "canonical_symbol",
        "trading_date",
        "adjusted_close",
    ]

    missing = [
        col for col in required_columns
        if col not in df.columns
    ]

    if missing:
        raise ValueError(f"Missing columns: {missing}")

    # Calculate returns using adjusted closing prices.
    df = df.with_columns([
        (
            pl.col("adjusted_close")
            / pl.col("adjusted_close").shift(1)
            - 1
        ).alias("daily_return"),

        (
            pl.col("adjusted_close")
            .log()
            - pl.col("adjusted_close").shift(1).log()
        ).alias("daily_log_return"),
    ])

    # Cumulative return from the first available observation.
    df = df.with_columns(
        (
            pl.col("adjusted_close")
            / pl.col("adjusted_close").drop_nulls().first()
            - 1
        ).alias("cumulative_return")
    )

    # Rolling returns in trading sessions.
    rolling_windows = [20, 60, 90, 252]

    expressions = []

    for window in rolling_windows:
        expressions.append(
            (
                pl.col("adjusted_close")
                / pl.col("adjusted_close").shift(window)
                - 1
            ).alias(f"rolling_return_{window}d")
        )

    # Annualized rolling volatility.
    for window in [20, 60, 252]:
        expressions.append(
            (
                pl.col("daily_return")
                .rolling_std(window_size=window)
                * (252 ** 0.5)
            ).alias(f"annualized_volatility_{window}d")
        )

    df = df.with_columns(expressions)

    # Calendar-period returns.
    df = df.with_columns([
        pl.col("trading_date").dt.strftime("%G-%V").alias(
            "week_period"
        ),
        pl.col("trading_date").dt.strftime("%Y-%m").alias(
            "month_period"
        ),
        pl.col("trading_date").dt.strftime("%Y-Q").alias(
            "quarter_year"
        ),
        pl.col("trading_date").dt.year().alias(
            "year_period"
        ),
    ])

    # Add processing metadata.
    df = df.with_columns(
        pl.lit(
            datetime.now(timezone.utc).isoformat()
        ).alias("returns_processed_at")
    )

    return df


def create_periodic_returns(df, period_column):

    periodic = (
        df.sort("trading_date")
        .group_by(period_column)
        .agg([
            pl.col("instrument_id").first(),
            pl.col("canonical_symbol").first(),
            pl.col("trading_date").min().alias("period_start"),
            pl.col("trading_date").max().alias("period_end"),
            pl.col("adjusted_close").first().alias("first_close"),
            pl.col("adjusted_close").last().alias("last_close"),
        ])
        .sort("period_start")
    )

    periodic = periodic.with_columns(
        (
            pl.col("last_close")
            / pl.col("last_close").shift(1)
            - 1
        ).alias("period_return")
    )

    return periodic


def upload_file(local_file, s3_key):

    s3.upload_file(
        str(local_file),
        BUCKET,
        s3_key,
    )

    logger.info("Uploaded: %s", s3_key)


def process_instrument(file_path):

    df = calculate_returns(file_path)

    symbol = str(df["canonical_symbol"][0])

    output_dir = OUTPUT_DIR / f"symbol={symbol}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Daily returns dataset.
    daily_file = output_dir / "daily_returns.parquet"

    df.write_parquet(
        daily_file,
        compression="zstd",
        statistics=True,
    )

    upload_file(
        daily_file,
        f"gold/returns/frequency=daily/symbol={symbol}/returns.parquet",
    )

    # Weekly, monthly, quarterly and annual datasets.
    periodic_configs = {
        "weekly": "week_period",
        "monthly": "month_period",
        "quarterly": "quarter_year",
        "annual": "year_period",
    }

    for frequency, column in periodic_configs.items():

        periodic = create_periodic_returns(df, column)

        output_file = (
            output_dir / f"{frequency}_returns.parquet"
        )

        periodic.write_parquet(
            output_file,
            compression="zstd",
        )

        upload_file(
            output_file,
            f"gold/returns/frequency={frequency}/symbol={symbol}/returns.parquet",
        )

    logger.info(
        "SUCCESS | %s | %s daily records",
        symbol,
        df.height,
    )


def main():

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    files = sorted(INPUT_DIR.glob("symbol=*/adjusted_prices.parquet"))

    successful = 0
    failed = 0

    for file_path in files:

        try:
            logger.info("Processing %s", file_path)

            process_instrument(file_path)

            successful += 1

        except Exception:
            logger.exception("Failed: %s", file_path)
            failed += 1

    print("\nQuantera Returns Engine")
    print("----------------------")
    print(f"Instruments processed: {successful + failed}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    main()

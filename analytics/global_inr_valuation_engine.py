
from pathlib import Path
from datetime import datetime, timezone
import logging

import polars as pl
import boto3

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

SYMBOLS = {
    "AAPL": "USD",
    "GOOGL": "USD",
    "MSFT": "USD",
    "NVDA": "USD",
    "INFY": "INR",
    "RELIANCE": "INR",
    "TCS": "INR",
}

FX_FILE = Path("data/silver/fx/USDINR.parquet")
PRICE_DIR = Path("data/gold/adjusted-prices")
OUTPUT_DIR = Path("data/gold/inr-valuations")

BUCKET = "quant-global-data-lake-prod"
REGION = "ap-southeast-2"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("QuanteraINRValuation")

s3 = boto3.client("s3", region_name=REGION)


# --------------------------------------------------
# LOAD DATA
# --------------------------------------------------

def load_inputs():

    if not FX_FILE.exists():
        raise FileNotFoundError(FX_FILE)

    fx = pl.read_parquet(FX_FILE).select([
        "trading_date",
        "usd_inr_rate"
    ]).rename({
        "trading_date": "fx_date"
    }).sort("fx_date")

    if fx.height == 0:
        raise ValueError("FX dataset is empty.")

    prices = {}

    calendar_parts = [
        fx.select(
            pl.col("fx_date").alias("valuation_date")
        )
    ]

    for symbol in SYMBOLS:

        path = (
            PRICE_DIR /
            f"symbol={symbol}" /
            "adjusted_prices.parquet"
        )

        if not path.exists():
            raise FileNotFoundError(path)

        df = pl.read_parquet(path).select([
            "trading_date",
            "adjusted_close",
            "instrument_id",
            "canonical_symbol"
        ])

        df = df.rename({
            "trading_date": "price_date"
        }).sort("price_date")

        if df.height == 0:
            raise ValueError(f"{symbol}: empty price dataset.")

        if df["price_date"].n_unique() != df.height:
            raise ValueError(f"{symbol}: duplicate price dates.")

        prices[symbol] = df

        calendar_parts.append(
            df.select(
                pl.col("price_date").alias("valuation_date")
            )
        )

    calendar = (
        pl.concat(calendar_parts)
        .unique()
        .sort("valuation_date")
    )

    return fx, prices, calendar


# --------------------------------------------------
# BUILD INR VALUATION
# --------------------------------------------------

def build_valuation(symbol, currency, prices, fx, calendar):

    logger.info("Building INR valuation for %s", symbol)

    # Carry forward last available security price
    result = calendar.join_asof(
        prices[symbol],
        left_on="valuation_date",
        right_on="price_date",
        strategy="backward"
    )

    if currency == "USD":

        # Carry forward latest available FX rate
        result = result.join_asof(
            fx,
            left_on="valuation_date",
            right_on="fx_date",
            strategy="backward"
        )

        result = result.with_columns(
            (
                pl.col("adjusted_close") *
                pl.col("usd_inr_rate")
            ).alias("inr_valuation")
        )

    else:

        result = result.with_columns([
            pl.lit(None, dtype=pl.Float64)
            .alias("usd_inr_rate"),

            pl.lit(None, dtype=pl.Date)
            .alias("fx_date"),

            pl.col("adjusted_close")
            .alias("inr_valuation")
        ])

    result = result.filter(
        pl.col("adjusted_close").is_not_null()
    ).sort("valuation_date")

    # Market availability flags
    result = result.with_columns([
        (
            pl.col("price_date") ==
            pl.col("valuation_date")
        ).alias("security_market_open"),

        (
            pl.col("fx_date") ==
            pl.col("valuation_date")
        ).fill_null(False).alias("fx_market_observation_available"),

        pl.lit(symbol).alias("canonical_symbol"),
        pl.lit(currency).alias("local_currency"),
        pl.lit("INR").alias("valuation_currency"),

        pl.lit(
            "ADJUSTED_CLOSE_ASOF_FX_CONVERSION"
        ).alias("valuation_method"),

        pl.lit(
            datetime.now(timezone.utc).isoformat()
        ).alias("valuation_processed_at")
    ])

    # Daily INR returns
    result = result.with_columns([
        pl.col("inr_valuation")
        .pct_change()
        .alias("daily_return_inr"),

        pl.col("inr_valuation")
        .log()
        .diff()
        .alias("daily_log_return_inr")
    ])

    # Data quality checks
    invalid = result.filter(
        pl.col("inr_valuation").is_null() |
        ~pl.col("inr_valuation").is_finite() |
        (pl.col("inr_valuation") <= 0)
    )

    if invalid.height > 0:
        raise ValueError(
            f"{symbol}: {invalid.height} invalid INR valuations."
        )

    if currency == "USD":
        missing_fx = result.filter(
            pl.col("usd_inr_rate").is_null() |
            (pl.col("usd_inr_rate") <= 0)
        )

        if missing_fx.height > 0:
            raise ValueError(
                f"{symbol}: missing valid FX rates."
            )

    return result


# --------------------------------------------------
# SAVE AND UPLOAD
# --------------------------------------------------

def save_and_upload(df, symbol):

    local_dir = OUTPUT_DIR / f"symbol={symbol}"
    local_dir.mkdir(parents=True, exist_ok=True)

    local_file = local_dir / "daily_valuation.parquet"

    df.write_parquet(
        local_file,
        compression="zstd"
    )

    s3_key = (
        f"gold/inr-valuations/"
        f"symbol={symbol}/daily_valuation.parquet"
    )

    s3.upload_file(
        str(local_file),
        BUCKET,
        s3_key
    )

    logger.info(
        "Uploaded s3://%s/%s",
        BUCKET,
        s3_key
    )


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():

    logger.info("Loading global equity and FX datasets...")

    fx, prices, calendar = load_inputs()

    logger.info(
        "Common valuation calendar: %s dates",
        calendar.height
    )

    successful = 0
    failed = 0

    for symbol, currency in SYMBOLS.items():

        try:

            result = build_valuation(
                symbol,
                currency,
                prices,
                fx,
                calendar
            )

            save_and_upload(result, symbol)

            print(
                f"{symbol} | {currency} to INR | "
                f"{result.height} observations | SUCCESS"
            )

            successful += 1

        except Exception:

            logger.exception("FAILED: %s", symbol)
            failed += 1

    print("\nQUANTERA GLOBAL INR VALUATION ENGINE")
    print("-------------------------------------")
    print(f"Instruments: {successful + failed}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    print(f"Calendar dates: {calendar.height}")

    if failed:
        raise RuntimeError(
            f"{failed} instruments failed."
        )


if __name__ == "__main__":
    main()

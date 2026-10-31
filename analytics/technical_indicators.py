from pathlib import Path
from datetime import datetime, timezone
import logging

import polars as pl
import boto3


# Configuration
INPUT_DIR = Path("data/gold/adjusted-prices")
OUTPUT_DIR = Path("data/gold/technical-indicators")

BUCKET = "quant-global-data-lake-prod"
REGION = "ap-southeast-2"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("Quantera.TechnicalIndicators")

s3 = boto3.client("s3", region_name=REGION)


def calculate_indicators(file_path):

    df = pl.read_parquet(file_path).sort("trading_date")

    required = [
        "instrument_id",
        "canonical_symbol",
        "trading_date",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
        "volume",
    ]

    missing = [c for c in required if c not in df.columns]

    if missing:
        raise ValueError(f"Missing columns: {missing}")

    close = pl.col("adjusted_close")
    high = pl.col("adjusted_high")
    low = pl.col("adjusted_low")
    volume = pl.col("volume")

    # -------------------------------------------------
    # 1. Simple Moving Averages
    # -------------------------------------------------

    sma_expressions = []

    for window in [5, 10, 20, 50, 100, 200]:
        sma_expressions.append(
            close.rolling_mean(window_size=window)
            .alias(f"sma_{window}")
        )

    df = df.with_columns(sma_expressions)

    # -------------------------------------------------
    # 2. Exponential Moving Averages
    # -------------------------------------------------

    ema_expressions = []

    for span in [9, 12, 20, 26, 50, 200]:
        ema_expressions.append(
            close.ewm_mean(
                span=span,
                adjust=False,
                min_samples=span,
            ).alias(f"ema_{span}")
        )

    df = df.with_columns(ema_expressions)

    # -------------------------------------------------
    # 3. RSI (14)
    # -------------------------------------------------

    df = df.with_columns(
        close.diff().alias("price_change")
    )

    df = df.with_columns([
        pl.when(pl.col("price_change") > 0)
        .then(pl.col("price_change"))
        .otherwise(0.0)
        .alias("gain"),

        pl.when(pl.col("price_change") < 0)
        .then(-pl.col("price_change"))
        .otherwise(0.0)
        .alias("loss"),
    ])

    df = df.with_columns([
        pl.col("gain")
        .ewm_mean(alpha=1 / 14, adjust=False, min_samples=14)
        .alias("average_gain"),

        pl.col("loss")
        .ewm_mean(alpha=1 / 14, adjust=False, min_samples=14)
        .alias("average_loss"),
    ])

    df = df.with_columns(
        pl.when(
            (pl.col("average_gain") == 0)
            & (pl.col("average_loss") == 0)
        )
        .then(50.0)
        .when(pl.col("average_loss") == 0)
        .then(100.0)
        .otherwise(
            100 - (
                100 / (
                    1 + pl.col("average_gain")
                    / pl.col("average_loss")
                )
            )
        )
        .alias("rsi_14")
    )

    # -------------------------------------------------
    # 4. MACD (12, 26, 9)
    # -------------------------------------------------

    df = df.with_columns(
        (
            pl.col("ema_12") - pl.col("ema_26")
        ).alias("macd_line")
    )

    df = df.with_columns(
        pl.col("macd_line")
        .ewm_mean(
            span=9,
            adjust=False,
            min_samples=9,
        )
        .alias("macd_signal")
    )

    df = df.with_columns(
        (
            pl.col("macd_line") - pl.col("macd_signal")
        ).alias("macd_histogram")
    )

    # -------------------------------------------------
    # 5. Bollinger Bands (20, 2)
    # -------------------------------------------------

    df = df.with_columns([
        close.rolling_mean(window_size=20).alias("bb_middle"),

        close.rolling_std(window_size=20).alias("bb_std"),
    ])

    df = df.with_columns([
        (
            pl.col("bb_middle") + 2 * pl.col("bb_std")
        ).alias("bb_upper"),

        (
            pl.col("bb_middle") - 2 * pl.col("bb_std")
        ).alias("bb_lower"),
    ])

    df = df.with_columns(
        (
            (close - pl.col("bb_lower"))
            / (pl.col("bb_upper") - pl.col("bb_lower"))
        ).alias("bb_percent_b")
    )

    # -------------------------------------------------
    # 6. Average True Range (ATR 14)
    # -------------------------------------------------

    df = df.with_columns(
        close.shift(1).alias("previous_close")
    )

    df = df.with_columns(
        pl.max_horizontal(
            high - low,
            (high - pl.col("previous_close")).abs(),
            (low - pl.col("previous_close")).abs(),
        ).alias("true_range")
    )

    df = df.with_columns(
        pl.col("true_range")
        .ewm_mean(alpha=1 / 14, adjust=False, min_samples=14)
        .alias("atr_14")
    )

    # -------------------------------------------------
    # 7. Stochastic Oscillator (14, 3)
    # -------------------------------------------------

    df = df.with_columns([
        low.rolling_min(window_size=14).alias("stoch_low_14"),

        high.rolling_max(window_size=14).alias("stoch_high_14"),
    ])

    df = df.with_columns(
        (
            100 * (close - pl.col("stoch_low_14"))
            / (pl.col("stoch_high_14") - pl.col("stoch_low_14"))
        ).alias("stoch_k")
    )

    df = df.with_columns(
        pl.col("stoch_k")
        .rolling_mean(window_size=3)
        .alias("stoch_d")
    )

    # -------------------------------------------------
    # 8. ADX (14)
    # -------------------------------------------------

    df = df.with_columns([
        (high - high.shift(1)).alias("up_move"),
        (low.shift(1) - low).alias("down_move"),
    ])

    df = df.with_columns([
        pl.when(
            (pl.col("up_move") > pl.col("down_move"))
            & (pl.col("up_move") > 0)
        )
        .then(pl.col("up_move"))
        .otherwise(0.0)
        .alias("plus_dm"),

        pl.when(
            (pl.col("down_move") > pl.col("up_move"))
            & (pl.col("down_move") > 0)
        )
        .then(pl.col("down_move"))
        .otherwise(0.0)
        .alias("minus_dm"),
    ])

    df = df.with_columns([
        pl.col("plus_dm")
        .ewm_mean(alpha=1 / 14, adjust=False, min_samples=14)
        .alias("smoothed_plus_dm"),

        pl.col("minus_dm")
        .ewm_mean(alpha=1 / 14, adjust=False, min_samples=14)
        .alias("smoothed_minus_dm"),
    ])

    df = df.with_columns([
        (
            100 * pl.col("smoothed_plus_dm") / pl.col("atr_14")
        ).alias("plus_di"),

        (
            100 * pl.col("smoothed_minus_dm") / pl.col("atr_14")
        ).alias("minus_di"),
    ])

    df = df.with_columns(
        (
            100 * (
                (pl.col("plus_di") - pl.col("minus_di")).abs()
                / (pl.col("plus_di") + pl.col("minus_di"))
            )
        ).alias("dx")
    )

    df = df.with_columns(
        pl.col("dx")
        .ewm_mean(alpha=1 / 14, adjust=False, min_samples=14)
        .alias("adx_14")
    )

    # -------------------------------------------------
    # 9. On-Balance Volume (OBV)
    # -------------------------------------------------

    df = df.with_columns(
        pl.when(close > close.shift(1))
        .then(volume)
        .when(close < close.shift(1))
        .then(-volume)
        .otherwise(0.0)
        .alias("obv_change")
    )

    df = df.with_columns(
        pl.col("obv_change").cum_sum().alias("obv")
    )

    # -------------------------------------------------
    # 10. Daily OHLCV-based VWAP proxy
    # -------------------------------------------------

    df = df.with_columns(
        (
            (high + low + close) / 3
        ).alias("typical_price")
    )

    df = df.with_columns([
        (pl.col("typical_price") * volume).alias("price_volume"),
        volume.alias("vwap_volume"),
    ])

    df = df.with_columns([
        pl.col("price_volume").cum_sum().alias("cumulative_price_volume"),
        pl.col("vwap_volume").cum_sum().alias("cumulative_volume"),
    ])

    df = df.with_columns(
        (
            pl.col("cumulative_price_volume")
            / pl.col("cumulative_volume")
        ).alias("cumulative_vwap_proxy")
    )

    # -------------------------------------------------
    # Metadata
    # -------------------------------------------------

    df = df.with_columns(
        pl.lit(
            datetime.now(timezone.utc).isoformat()
        ).alias("indicators_processed_at")
    )

    # Remove temporary calculation columns.
    temporary_columns = [
        "price_change", "gain", "loss",
        "average_gain", "average_loss",
        "previous_close", "true_range",
        "up_move", "down_move",
        "plus_dm", "minus_dm",
        "smoothed_plus_dm", "smoothed_minus_dm",
        "plus_di", "minus_di", "dx",
        "obv_change", "bb_std",
        "stoch_low_14", "stoch_high_14",
        "typical_price", "price_volume",
        "vwap_volume", "cumulative_price_volume",
        "cumulative_volume",
    ]

    df = df.drop([
        column for column in temporary_columns
        if column in df.columns
    ])

    return df


def process_file(file_path):

    df = calculate_indicators(file_path)

    symbol = str(df["canonical_symbol"][0])

    output_dir = OUTPUT_DIR / f"symbol={symbol}"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "technical_indicators.parquet"

    df.write_parquet(
        output_file,
        compression="zstd",
        statistics=True,
    )

    s3_key = (
        f"gold/technical-indicators/"
        f"symbol={symbol}/technical_indicators.parquet"
    )

    s3.upload_file(
        str(output_file),
        BUCKET,
        s3_key,
    )

    logger.info(
        "SUCCESS | %s | %s records | %s",
        symbol,
        df.height,
        s3_key,
    )


def main():

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    files = sorted(
        INPUT_DIR.glob("symbol=*/adjusted_prices.parquet")
    )

    successful = 0
    failed = 0

    for file_path in files:

        try:
            logger.info("Processing %s", file_path)
            process_file(file_path)
            successful += 1

        except Exception:
            logger.exception("FAILED: %s", file_path)
            failed += 1

    print("\nQuantera Technical Indicators Engine")
    print("------------------------------------")
    print(f"Instruments processed: {successful + failed}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    main()

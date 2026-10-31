
from pathlib import Path
from datetime import date

import polars as pl
import numpy as np
import boto3
import logging

SYMBOLS = [
    "AAPL", "GOOGL", "MSFT", "NVDA",
    "INFY", "RELIANCE", "TCS"
]

INPUT_DIR = Path("data/gold/inr-valuations")
OUTPUT_DIR = Path("data/gold/global-weekly")

BUCKET = "quant-global-data-lake-prod"
REGION = "ap-southeast-2"

TRADING_WEEKS = 52

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("QuanteraGlobalWeekly")

s3 = boto3.client("s3", region_name=REGION)


def load_weekly_valuations():

    datasets = []

    for symbol in SYMBOLS:

        path = (
            INPUT_DIR /
            f"symbol={symbol}" /
            "daily_valuation.parquet"
        )

        df = pl.read_parquet(path).sort("valuation_date")

        aggregations = [
            pl.col("inr_valuation").last().alias(symbol),

            pl.col("price_date")
            .last()
            .alias(f"{symbol}_source_date"),

            pl.col("security_market_open")
            .last()
            .alias(f"{symbol}_market_open"),
        ]

        if "fx_date" in df.columns:
            aggregations.append(
                pl.col("fx_date")
                .last()
                .alias(f"{symbol}_fx_source_date")
            )

        weekly = (
            df.group_by_dynamic(
                "valuation_date",
                every="1w",
                period="1w",
                closed="right",
                label="right"
            )
            .agg(aggregations)
        )

        datasets.append(weekly)

    combined = datasets[0]

    for df in datasets[1:]:

        combined = combined.join(
            df,
            on="valuation_date",
            how="inner"
        )

    return combined.sort("valuation_date")


def add_observation_metadata(valuations):

    source_columns = [
        f"{symbol}_source_date"
        for symbol in SYMBOLS
    ]

    valuations = valuations.with_columns(
        pl.max_horizontal(source_columns)
        .alias("actual_observation_date")
    )

    return valuations


def calculate_weekly_returns(valuations):

    returns = valuations.select([
        (
            pl.col(symbol) /
            pl.col(symbol).shift(1) - 1
        ).alias(symbol)
        for symbol in SYMBOLS
    ])

    metadata_columns = [
        "valuation_date",
        "actual_observation_date"
    ]

    returns = (
        returns
        .with_columns([
            valuations[col]
            for col in metadata_columns
        ])
        .drop_nulls(SYMBOLS)
        .sort("valuation_date")
    )

    return returns


def calculate_statistics(returns):

    matrix = returns.select(SYMBOLS).to_numpy()

    if matrix.shape[0] < 52:
        raise ValueError(
            "Insufficient weekly observations."
        )

    if not np.isfinite(matrix).all():
        raise ValueError(
            "Non-finite weekly returns detected."
        )

    correlation = np.corrcoef(
        matrix,
        rowvar=False
    )

    covariance = (
        np.cov(matrix, rowvar=False, ddof=1)
        * TRADING_WEEKS
    )

    volatility = (
        np.std(matrix, axis=0, ddof=1)
        * np.sqrt(TRADING_WEEKS)
    )

    correlation_df = pl.DataFrame(
        correlation,
        schema=SYMBOLS,
        orient="row"
    ).with_columns(
        pl.Series("symbol", SYMBOLS)
    ).select(["symbol"] + SYMBOLS)

    covariance_df = pl.DataFrame(
        covariance,
        schema=SYMBOLS,
        orient="row"
    ).with_columns(
        pl.Series("symbol", SYMBOLS)
    ).select(["symbol"] + SYMBOLS)

    volatility_df = pl.DataFrame({
        "symbol": SYMBOLS,
        "annualized_volatility": volatility
    })

    return (
        correlation_df,
        covariance_df,
        volatility_df
    )


def upload(df, filename, key):

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    path = OUTPUT_DIR / filename

    df.write_parquet(
        path,
        compression="zstd"
    )

    s3.upload_file(
        str(path),
        BUCKET,
        key
    )

    logger.info("Uploaded %s", key)


def main():

    logger.info("Loading global INR valuations...")

    valuations = load_weekly_valuations()

    valuations = add_observation_metadata(
        valuations
    )

    # Do not include incomplete weekly observations
    # whose actual observation date is in the future.
    today = date.today()

    valuations = valuations.filter(
        pl.col("actual_observation_date") <= today
    )

    returns = calculate_weekly_returns(
        valuations
    )

    correlation, covariance, volatility = (
        calculate_statistics(returns)
    )

    upload(
        valuations,
        "weekly_valuations.parquet",
        "gold/global-weekly/weekly_valuations.parquet"
    )

    upload(
        returns,
        "weekly_returns.parquet",
        "gold/global-weekly/weekly_returns.parquet"
    )

    upload(
        correlation,
        "correlation_matrix.parquet",
        "gold/global-weekly/correlation_matrix.parquet"
    )

    upload(
        covariance,
        "covariance_matrix.parquet",
        "gold/global-weekly/covariance_matrix.parquet"
    )

    upload(
        volatility,
        "annualized_volatility.parquet",
        "gold/global-weekly/annualized_volatility.parquet"
    )

    print("\nQUANTERA GLOBAL WEEKLY ANALYTICS")
    print("--------------------------------")
    print("Weekly observations:", returns.height)
    print("Instruments:", len(SYMBOLS))
    print(
        "Last weekly label:",
        valuations["valuation_date"].max()
    )
    print(
        "Last actual observation:",
        valuations["actual_observation_date"].max()
    )

    print("\nAnnualized volatility:")
    print(volatility)

    print("\nCorrelation matrix:")
    print(correlation)

    print("\nStatus: SUCCESS")


if __name__ == "__main__":
    main()

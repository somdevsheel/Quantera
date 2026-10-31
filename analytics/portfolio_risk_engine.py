
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import polars as pl
import boto3
import logging

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

INPUT_DIR = Path("data/gold/returns")
OUTPUT_DIR = Path("data/gold/portfolio-risk")

BUCKET = "quant-global-data-lake-prod"
REGION = "ap-southeast-2"

WEIGHTS = {
    "AAPL": 1 / 7,
    "GOOGL": 1 / 7,
    "INFY": 1 / 7,
    "MSFT": 1 / 7,
    "NVDA": 1 / 7,
    "RELIANCE": 1 / 7,
    "TCS": 1 / 7,
}

RISK_FREE_RATE = 0.06
TRADING_DAYS = 252

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("QuanteraPortfolioRisk")

s3 = boto3.client("s3", region_name=REGION)


# --------------------------------------------------
# LOAD AND ALIGN RETURNS
# --------------------------------------------------

def load_returns():

    datasets = []

    for symbol in WEIGHTS:

        path = INPUT_DIR / f"symbol={symbol}" / "daily_returns.parquet"

        if not path.exists():
            raise FileNotFoundError(f"Missing returns file: {path}")

        df = pl.read_parquet(path)

        df = df.select([
            "trading_date",
            "daily_return"
        ]).rename({
            "daily_return": symbol
        })

        datasets.append(df)

    combined = datasets[0]

    for df in datasets[1:]:
        combined = combined.join(
            df,
            on="trading_date",
            how="inner"
        )

    combined = combined.sort("trading_date")

    combined = combined.drop_nulls()

    combined = combined.filter(
        pl.all_horizontal([
            pl.col(symbol).is_finite()
            for symbol in WEIGHTS
        ])
    )

    if combined.height < 252:
        raise ValueError("Insufficient aligned daily observations.")

    return combined


# --------------------------------------------------
# PORTFOLIO RETURNS
# --------------------------------------------------

def calculate_portfolio_returns(df):

    symbols = list(WEIGHTS.keys())

    returns = df.select(symbols).to_numpy()

    weights = np.array([
        WEIGHTS[symbol] for symbol in symbols
    ])

    portfolio_returns = returns @ weights

    return returns, weights, portfolio_returns


# --------------------------------------------------
# RISK METRICS
# --------------------------------------------------

def calculate_metrics(returns):

    returns = np.asarray(returns, dtype=float)

    returns = returns[np.isfinite(returns)]

    if len(returns) < 2:
        raise ValueError("Insufficient observations.")

    if np.any(returns <= -1):
        raise ValueError("Returns contain values <= -100%.")

    mean_daily = np.mean(returns)
    volatility_daily = np.std(returns, ddof=1)

    annualized_return = (
        np.prod(1 + returns) ** (TRADING_DAYS / len(returns))
    ) - 1

    annualized_volatility = volatility_daily * np.sqrt(TRADING_DAYS)

    daily_rf = (1 + RISK_FREE_RATE) ** (1 / TRADING_DAYS) - 1

    excess_returns = returns - daily_rf

    sharpe = (
        np.mean(excess_returns) / volatility_daily * np.sqrt(TRADING_DAYS)
        if volatility_daily > 0 else None
    )

    downside = np.minimum(excess_returns, 0)

    downside_deviation = np.sqrt(np.mean(downside ** 2)) * np.sqrt(TRADING_DAYS)

    sortino = (
        (np.mean(excess_returns) * TRADING_DAYS) / downside_deviation
        if downside_deviation > 0 else None
    )

    wealth = np.concatenate([
        [1.0],
        np.cumprod(1 + returns)
    ])

    running_peak = np.maximum.accumulate(wealth)

    drawdowns = wealth / running_peak - 1

    max_drawdown = np.min(drawdowns)

    var_95 = np.quantile(returns, 0.05)
    var_99 = np.quantile(returns, 0.01)

    cvar_95 = np.mean(returns[returns <= var_95])
    cvar_99 = np.mean(returns[returns <= var_99])

    calmar = (
        annualized_return / abs(max_drawdown)
        if max_drawdown < 0 else None
    )

    return {
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_volatility,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "downside_deviation": downside_deviation,
        "max_drawdown": max_drawdown,
        "var_95": var_95,
        "var_99": var_99,
        "cvar_95": cvar_95,
        "cvar_99": cvar_99,
        "calmar_ratio": calmar,
        "observations": len(returns),
    }


# --------------------------------------------------
# CORRELATION AND COVARIANCE
# --------------------------------------------------

def calculate_matrices(returns, symbols):

    correlation = np.corrcoef(returns, rowvar=False)

    covariance = np.cov(
        returns,
        rowvar=False,
        ddof=1
    ) * TRADING_DAYS

    correlation_df = pl.DataFrame(
        correlation,
        schema=symbols,
        orient="row"
    ).with_columns(
        pl.Series("symbol", symbols)
    ).select(["symbol"] + symbols)

    covariance_df = pl.DataFrame(
        covariance,
        schema=symbols,
        orient="row"
    ).with_columns(
        pl.Series("symbol", symbols)
    ).select(["symbol"] + symbols)

    return correlation_df, covariance_df


# --------------------------------------------------
# SAVE AND UPLOAD
# --------------------------------------------------

def save_and_upload(df, filename, s3_key):

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    local_path = OUTPUT_DIR / filename

    df.write_parquet(
        local_path,
        compression="zstd"
    )

    s3.upload_file(
        str(local_path),
        BUCKET,
        s3_key
    )

    logger.info("Uploaded s3://%s/%s", BUCKET, s3_key)


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():

    logger.info("Loading aligned instrument returns...")

    df = load_returns()

    symbols = list(WEIGHTS.keys())

    returns, weights, portfolio_returns = calculate_portfolio_returns(df)

    metrics = calculate_metrics(portfolio_returns)

    correlation_df, covariance_df = calculate_matrices(
        returns,
        symbols
    )

    timestamp = datetime.now(timezone.utc).isoformat()

    metrics["portfolio_name"] = "EQUAL_WEIGHT_7_ASSET"
    metrics["currency_basis"] = "MIXED_LOCAL_CURRENCY_NOT_FX_ADJUSTED"
    metrics["risk_free_rate"] = RISK_FREE_RATE
    metrics["calculation_timestamp"] = timestamp

    metrics_df = pl.DataFrame([metrics])

    portfolio_df = pl.DataFrame({
        "trading_date": df["trading_date"],
        "portfolio_return": portfolio_returns,
        "portfolio_wealth": np.cumprod(1 + portfolio_returns),
    })

    weights_df = pl.DataFrame({
        "symbol": symbols,
        "weight": weights,
    })

    save_and_upload(
        metrics_df,
        "portfolio_metrics.parquet",
        "gold/portfolio-risk/portfolio_metrics.parquet"
    )

    save_and_upload(
        portfolio_df,
        "portfolio_returns.parquet",
        "gold/portfolio-risk/portfolio_returns.parquet"
    )

    save_and_upload(
        correlation_df,
        "correlation_matrix.parquet",
        "gold/portfolio-risk/correlation_matrix.parquet"
    )

    save_and_upload(
        covariance_df,
        "covariance_matrix.parquet",
        "gold/portfolio-risk/covariance_matrix.parquet"
    )

    save_and_upload(
        weights_df,
        "portfolio_weights.parquet",
        "gold/portfolio-risk/portfolio_weights.parquet"
    )

    print("\nQUANTERA PORTFOLIO RISK ENGINE")
    print("--------------------------------")

    for key, value in metrics.items():
        print(f"{key}: {value}")

    print("\nPortfolio weights:")
    print(weights_df)

    print("\nCorrelation matrix:")
    print(correlation_df)

    print("\nAll portfolio datasets uploaded successfully.")


if __name__ == "__main__":
    main()

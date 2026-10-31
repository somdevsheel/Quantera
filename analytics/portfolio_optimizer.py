
from pathlib import Path
import logging
import os

import boto3
import numpy as np
import polars as pl

from dotenv import load_dotenv
from scipy.optimize import minimize, linprog


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

load_dotenv()

SYMBOLS = [
    "AAPL", "GOOGL", "MSFT", "NVDA",
    "INFY", "RELIANCE", "TCS"
]

PERIODS_PER_YEAR = 52
RISK_FREE_RATE = 0.06
MAX_WEIGHT = 0.30
MIN_WEIGHT = 0.0

FRONTIER_POINTS = 25

INPUT_FILE = Path(
    "data/gold/global-weekly/weekly_returns.parquet"
)

OUTPUT_DIR = Path("data/gold/portfolio-optimization")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-2")
S3_BUCKET = os.getenv(
    "S3_BUCKET", "quant-global-data-lake-prod"
)

S3_PREFIX = "gold/portfolio-optimization/"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("quantera.optimizer")


# --------------------------------------------------
# LOAD DATA
# --------------------------------------------------

def load_returns():

    if not INPUT_FILE.exists():
        raise FileNotFoundError(INPUT_FILE)

    df = pl.read_parquet(INPUT_FILE).sort("valuation_date")

    missing = [
        symbol for symbol in SYMBOLS
        if symbol not in df.columns
    ]

    if missing:
        raise ValueError(
            f"Missing return columns: {missing}. "
            f"Available columns: {df.columns}"
        )

    df = df.select(
        ["valuation_date"] + SYMBOLS
    )

    df = df.drop_nulls(SYMBOLS)

    df = df.filter(
        pl.all_horizontal([
            pl.col(symbol).is_finite()
            for symbol in SYMBOLS
        ])
    )

    returns = df.select(SYMBOLS).to_numpy()

    if returns.shape[0] < 52:
        raise ValueError(
            "At least 52 complete weekly observations are required."
        )

    if not np.isfinite(returns).all():
        raise ValueError("Non-finite returns detected.")

    logger.info(
        "Loaded %s weekly observations for %s instruments",
        returns.shape[0],
        len(SYMBOLS)
    )

    return df, returns


# --------------------------------------------------
# ESTIMATE RETURNS AND COVARIANCE
# --------------------------------------------------

def estimate_parameters(returns):

    # Arithmetic weekly mean annualized
    expected_returns = returns.mean(axis=0) * PERIODS_PER_YEAR

    # Sample covariance annualized
    sample_cov = np.cov(
        returns,
        rowvar=False,
        ddof=1
    ) * PERIODS_PER_YEAR

    # Shrink covariance toward its diagonal
    # This reduces sensitivity to noisy correlations.
    shrinkage = 0.05

    diagonal_cov = np.diag(np.diag(sample_cov))

    covariance = (
        (1 - shrinkage) * sample_cov
        + shrinkage * diagonal_cov
    )

    # Numerical regularization
    ridge = max(
        np.trace(covariance) / len(SYMBOLS) * 1e-8,
        1e-12
    )

    covariance += np.eye(len(SYMBOLS)) * ridge

    return expected_returns, covariance


# --------------------------------------------------
# PORTFOLIO METRICS
# --------------------------------------------------

def portfolio_metrics(weights, expected_returns, covariance):

    portfolio_return = float(
        weights @ expected_returns
    )

    portfolio_variance = float(
        weights @ covariance @ weights
    )

    volatility = float(
        np.sqrt(max(portfolio_variance, 0))
    )

    sharpe = (
        (portfolio_return - RISK_FREE_RATE) / volatility
        if volatility > 0 else 0.0
    )

    return {
        "expected_annual_return": portfolio_return,
        "annualized_volatility": volatility,
        "sharpe_ratio": sharpe,
        "risk_free_rate": RISK_FREE_RATE,
    }


# --------------------------------------------------
# OPTIMIZATION CONSTRAINTS
# --------------------------------------------------

N = len(SYMBOLS)

BOUNDS = [
    (MIN_WEIGHT, MAX_WEIGHT)
    for _ in range(N)
]

CONSTRAINTS = [
    {
        "type": "eq",
        "fun": lambda w: np.sum(w) - 1.0
    }
]

INITIAL_WEIGHTS = np.ones(N) / N


# --------------------------------------------------
# GLOBAL MINIMUM VARIANCE
# --------------------------------------------------

def optimize_minimum_variance(covariance):

    def objective(weights):
        return float(weights @ covariance @ weights)

    result = minimize(
        objective,
        INITIAL_WEIGHTS,
        method="SLSQP",
        bounds=BOUNDS,
        constraints=CONSTRAINTS,
        options={
            "maxiter": 2000,
            "ftol": 1e-12
        }
    )

    if not result.success:
        raise RuntimeError(
            f"Minimum variance optimization failed: {result.message}"
        )

    return result.x


# --------------------------------------------------
# MAXIMUM SHARPE RATIO
# --------------------------------------------------

def optimize_maximum_sharpe(
    expected_returns,
    covariance
):

    def objective(weights):

        metrics = portfolio_metrics(
            weights,
            expected_returns,
            covariance
        )

        return -metrics["sharpe_ratio"]

    result = minimize(
        objective,
        INITIAL_WEIGHTS,
        method="SLSQP",
        bounds=BOUNDS,
        constraints=CONSTRAINTS,
        options={
            "maxiter": 3000,
            "ftol": 1e-12
        }
    )

    if not result.success:
        raise RuntimeError(
            f"Maximum Sharpe optimization failed: {result.message}"
        )

    return result.x


# --------------------------------------------------
# RISK PARITY
# --------------------------------------------------

def optimize_risk_parity(covariance):

    def objective(weights):

        portfolio_vol = np.sqrt(
            max(weights @ covariance @ weights, 1e-16)
        )

        marginal_risk = covariance @ weights

        risk_contributions = (
            weights * marginal_risk / portfolio_vol
        )

        target = portfolio_vol / N

        return float(
            np.sum((risk_contributions - target) ** 2)
        )

    result = minimize(
        objective,
        INITIAL_WEIGHTS,
        method="SLSQP",
        bounds=BOUNDS,
        constraints=CONSTRAINTS,
        options={
            "maxiter": 3000,
            "ftol": 1e-12
        }
    )

    if not result.success:
        raise RuntimeError(
            f"Risk parity optimization failed: {result.message}"
        )

    return result.x


# --------------------------------------------------
# EFFICIENT FRONTIER
# --------------------------------------------------

def calculate_efficient_frontier(
    expected_returns,
    covariance
):

    min_return_result = linprog(
        expected_returns,
        A_eq=[np.ones(N)],
        b_eq=[1.0],
        bounds=BOUNDS,
        method="highs"
    )

    max_return_result = linprog(
        -expected_returns,
        A_eq=[np.ones(N)],
        b_eq=[1.0],
        bounds=BOUNDS,
        method="highs"
    )

    if not min_return_result.success or not max_return_result.success:
        raise RuntimeError("Could not determine frontier return bounds.")

    min_return = float(min_return_result.fun)
    max_return = float(-max_return_result.fun)

    targets = np.linspace(
        min_return,
        max_return,
        FRONTIER_POINTS
    )

    rows = []

    for target in targets:

        constraints = [
            *CONSTRAINTS,
            {
                "type": "eq",
                "fun": lambda w, t=target:
                    float(w @ expected_returns - t)
            }
        ]

        result = minimize(
            lambda w: float(w @ covariance @ w),
            INITIAL_WEIGHTS,
            method="SLSQP",
            bounds=BOUNDS,
            constraints=constraints,
            options={
                "maxiter": 2000,
                "ftol": 1e-11
            }
        )

        if not result.success:
            logger.warning(
                "Frontier point skipped: target %.6f",
                target
            )
            continue

        metrics = portfolio_metrics(
            result.x,
            expected_returns,
            covariance
        )

        rows.append({
            "target_return": target,
            **metrics
        })

    return pl.DataFrame(rows)


# --------------------------------------------------
# RISK CONTRIBUTIONS
# --------------------------------------------------

def calculate_risk_contributions(weights, covariance):

    portfolio_vol = np.sqrt(
        weights @ covariance @ weights
    )

    marginal_risk = covariance @ weights

    contributions = (
        weights * marginal_risk / portfolio_vol
    )

    return pl.DataFrame({
        "symbol": SYMBOLS,
        "weight": weights,
        "risk_contribution": contributions,
        "risk_contribution_pct": (
            contributions / portfolio_vol * 100
        )
    })


# --------------------------------------------------
# SAVE OUTPUTS
# --------------------------------------------------

def save_and_upload(name, df, s3):

    local_path = OUTPUT_DIR / f"{name}.parquet"

    df.write_parquet(
        local_path,
        compression="zstd"
    )

    s3_key = f"{S3_PREFIX}{name}.parquet"

    s3.upload_file(
        str(local_path),
        S3_BUCKET,
        s3_key
    )

    logger.info("Uploaded s3://%s/%s", S3_BUCKET, s3_key)


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():

    logger.info("Starting Quantera Portfolio Optimizer")

    _, returns = load_returns()

    expected_returns, covariance = estimate_parameters(
        returns
    )

    strategies = {
        "GLOBAL_MINIMUM_VARIANCE":
            optimize_minimum_variance(covariance),

        "MAXIMUM_SHARPE":
            optimize_maximum_sharpe(
                expected_returns,
                covariance
            ),

        "RISK_PARITY":
            optimize_risk_parity(covariance),
    }

    metric_rows = []
    weight_rows = []

    for strategy, weights in strategies.items():

        metrics = portfolio_metrics(
            weights,
            expected_returns,
            covariance
        )

        metric_rows.append({
            "strategy": strategy,
            **metrics
        })

        for symbol, weight in zip(SYMBOLS, weights):

            weight_rows.append({
                "strategy": strategy,
                "symbol": symbol,
                "weight": float(weight)
            })

    metrics_df = pl.DataFrame(metric_rows)
    weights_df = pl.DataFrame(weight_rows)

    frontier_df = calculate_efficient_frontier(
        expected_returns,
        covariance
    )

    risk_df = pl.concat([
        calculate_risk_contributions(
            weights,
            covariance
        ).with_columns(
            pl.lit(strategy).alias("strategy")
        )
        for strategy, weights in strategies.items()
    ])

    expected_df = pl.DataFrame({
        "symbol": SYMBOLS,
        "expected_annual_return": expected_returns,
        "annualized_volatility": np.sqrt(
            np.diag(covariance)
        )
    })

    covariance_df = pl.DataFrame(
        covariance,
        schema=SYMBOLS,
        orient="row"
    ).insert_column(
        0,
        pl.Series("symbol", SYMBOLS)
    )

    correlation = covariance / np.outer(
        np.sqrt(np.diag(covariance)),
        np.sqrt(np.diag(covariance))
    )

    correlation_df = pl.DataFrame(
        correlation,
        schema=SYMBOLS,
        orient="row"
    ).insert_column(
        0,
        pl.Series("symbol", SYMBOLS)
    )

    s3 = boto3.client(
        "s3",
        region_name=AWS_REGION
    )

    outputs = {
        "strategy_metrics": metrics_df,
        "strategy_weights": weights_df,
        "efficient_frontier": frontier_df,
        "risk_contributions": risk_df,
        "expected_returns": expected_df,
        "covariance_matrix": covariance_df,
        "correlation_matrix": correlation_df,
    }

    for name, df in outputs.items():
        save_and_upload(name, df, s3)

    print("\nQUANTERA PORTFOLIO OPTIMIZATION")
    print("--------------------------------")
    print("\nStrategy metrics:")
    print(metrics_df)

    print("\nPortfolio allocations:")
    print(
        weights_df.pivot(
            on="symbol",
            index="strategy",
            values="weight"
        )
    )

    print("\nRisk contributions:")
    print(risk_df)

    print("\nEfficient frontier:")
    print(frontier_df)

    print("\nStatus: SUCCESS")


if __name__ == "__main__":
    main()

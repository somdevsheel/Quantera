from pathlib import Path
from datetime import datetime, timezone
import logging

import polars as pl
import numpy as np
import boto3


# Configuration
INPUT_DIR = Path("data/gold/returns")
OUTPUT_DIR = Path("data/gold/risk")

BUCKET = "quant-global-data-lake-prod"
REGION = "ap-southeast-2"

TRADING_DAYS = 252
RISK_FREE_RATE = 0.06  # Annualized assumption: 6%

CONFIDENCE_LEVELS = [0.95, 0.99]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("Quantera.Risk")

s3 = boto3.client("s3", region_name=REGION)


def calculate_max_drawdown(returns):

    equity = np.cumprod(1 + returns)

    running_peak = np.maximum.accumulate(
        np.insert(equity, 0, 1.0)
    )[1:]

    drawdowns = equity / running_peak - 1

    return float(np.min(drawdowns))


def calculate_metrics(df):

    returns = (
        df["daily_return"]
        .drop_nulls()
        .to_numpy()
    )

    if len(returns) < 2:
        raise ValueError("Insufficient return observations.")

    if not np.isfinite(returns).all():
        raise ValueError("Returns contain non-finite values.")

    mean_daily = float(np.mean(returns))
    std_daily = float(np.std(returns, ddof=1))

    annualized_return = (
        (np.prod(1 + returns) ** (TRADING_DAYS / len(returns))) - 1
    )

    annualized_volatility = std_daily * np.sqrt(TRADING_DAYS)

    # Sharpe ratio
    daily_risk_free = (
        (1 + RISK_FREE_RATE) ** (1 / TRADING_DAYS) - 1
    )

    excess_returns = returns - daily_risk_free

    sharpe_ratio = (
        float(np.mean(excess_returns) / std_daily * np.sqrt(TRADING_DAYS))
        if std_daily > 0 else None
    )

    # Downside deviation and Sortino ratio
    downside_returns = np.minimum(excess_returns, 0)

    downside_deviation = float(
        np.sqrt(np.mean(downside_returns ** 2)) * np.sqrt(TRADING_DAYS)
    )

    sortino_ratio = (
        float(
            np.mean(excess_returns) * TRADING_DAYS
            / downside_deviation
        )
        if downside_deviation > 0 else None
    )

    # Historical VaR and CVaR
    var_results = {}

    for confidence in CONFIDENCE_LEVELS:

        percentile = 1 - confidence

        var = float(np.quantile(returns, percentile))

        tail_returns = returns[returns <= var]

        cvar = (
            float(np.mean(tail_returns))
            if len(tail_returns) else var
        )

        var_results[f"historical_var_{int(confidence * 100)}"] = var

        var_results[f"historical_cvar_{int(confidence * 100)}"] = cvar

    # Maximum drawdown
    max_drawdown = calculate_max_drawdown(returns)

    # Calmar ratio
    calmar_ratio = (
        float(annualized_return / abs(max_drawdown))
        if max_drawdown < 0 else None
    )

    return {
        "observations": len(returns),
        "annualized_return": float(annualized_return),
        "annualized_volatility": float(annualized_volatility),
        "daily_mean_return": mean_daily,
        "daily_standard_deviation": std_daily,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "downside_deviation": downside_deviation,
        "maximum_drawdown": max_drawdown,
        "calmar_ratio": calmar_ratio,
        **var_results,
    }


def process_instrument(file_path):

    df = pl.read_parquet(file_path).sort("trading_date")

    symbol = str(df["canonical_symbol"][0])

    metrics = calculate_metrics(df)

    result = {
        "instrument_id": str(df["instrument_id"][0]),
        "canonical_symbol": symbol,
        "frequency": "DAILY",
        "risk_free_rate_assumption": RISK_FREE_RATE,
        "trading_days_assumption": TRADING_DAYS,
        "calculation_timestamp": datetime.now(
            timezone.utc
        ).isoformat(),
        **metrics,
    }

    result_df = pl.DataFrame([result])

    output_dir = OUTPUT_DIR / f"symbol={symbol}"

    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "risk_metrics.parquet"

    result_df.write_parquet(
        output_file,
        compression="zstd",
    )

    s3_key = (
        f"gold/risk/frequency=daily/"
        f"symbol={symbol}/risk_metrics.parquet"
    )

    s3.upload_file(
        str(output_file),
        BUCKET,
        s3_key,
    )

    logger.info(
        "SUCCESS | %s | Sharpe: %s | Volatility: %.4f",
        symbol,
        metrics["sharpe_ratio"],
        metrics["annualized_volatility"],
    )


def main():

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    files = sorted(
        INPUT_DIR.glob("symbol=*/daily_returns.parquet")
    )

    if not files:
        print("ERROR: No daily returns files found.")
        print(f"Expected location: {INPUT_DIR}/symbol=*/daily_returns.parquet")
        return

    successful = 0
    failed = 0

    for file_path in files:

        try:
            logger.info("Processing %s", file_path)
            process_instrument(file_path)
            successful += 1

        except Exception:
            logger.exception("FAILED: %s", file_path)
            failed += 1

    print("\nQuantera Risk Analytics Engine")
    print("--------------------------------")
    print(f"Instruments processed: {successful + failed}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    main()


import argparse
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import boto3
import pandas as pd
import yfinance as yf

BUCKET = "quant-global-data-lake-prod"
REGION = "ap-southeast-2"

START_DATE = "2006-01-01"

def get_paths(asset_class):
    asset = asset_class.lower()
    base = {"equity":"market", "etf":"etf", "index":"indices"}.get(asset)
    if base is None:
        raise ValueError(f"Unsupported asset class: {asset_class}")
    staging = Path(f"data/staging/{base}")
    staging.mkdir(parents=True, exist_ok=True)
    return base, staging


def get_s3_client():
    return boto3.client("s3", region_name=REGION)


def download_yahoo_history(symbol, start=START_DATE):
    print(f"\nDownloading {symbol} from Yahoo Finance...")

    ticker = yf.Ticker(symbol)

    end = (date.today() + timedelta(days=1)).isoformat()

    df = ticker.history(
        start=start,
        end=end,
        interval="1d",
        auto_adjust=False,
        actions=True,
        repair=False,
        raise_errors=True,
    )

    if df.empty:
        print(f"No historical data available for {symbol}")
        return None

    df.index.name = "trading_date"
    df = df.reset_index()

    df.columns = [
        str(column).strip().lower().replace(" ", "_")
        for column in df.columns
    ]

    required = ["open", "high", "low", "close", "volume"]

    missing = [column for column in required if column not in df.columns]

    if missing:
        raise ValueError(f"Missing columns for {symbol}: {missing}")

    df["symbol"] = symbol
    df["provider"] = "yahoo_finance"
    df["ingested_at"] = datetime.now(timezone.utc).isoformat()
    df["price_adjustment_basis"] = "unadjusted_ohlc_with_adj_close"

    df["trading_date"] = pd.to_datetime(
        df["trading_date"]
    ).dt.date


    df = df.sort_values("trading_date")


    return df


def save_and_upload(df, symbol, asset_class="EQUITY"):
    base, staging_dir = get_paths(asset_class)
    safe_symbol = symbol.replace("^", "").replace(".", "_")
    s3 = get_s3_client()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")

    bronze_dir = Path(f"data/bronze/{base}")
    quarantine_dir = Path(f"data/quarantine/{base}")
    bronze_dir.mkdir(parents=True, exist_ok=True)
    quarantine_dir.mkdir(parents=True, exist_ok=True)

    def upload(frame, local_file, s3_key):
        frame.to_parquet(local_file, engine="pyarrow",
                         compression="zstd", index=False)
        s3.upload_file(
            str(local_file), BUCKET, s3_key,
            ExtraArgs={"ContentType": "application/vnd.apache.parquet"},
        )

    # Preserve the downloaded records first.
    raw_file = bronze_dir / f"{safe_symbol}_{run_id}.parquet"
    raw_key = (
        f"bronze/{base}/provider=yahoo_finance/symbol={safe_symbol}/"
        f"run_id={run_id}/daily_bars.parquet"
    )
    upload(df, raw_file, raw_key)

    required = ["open", "high", "low", "close", "volume"]
    numeric = df[required].apply(pd.to_numeric, errors="coerce")
    reasons = pd.Series("", index=df.index, dtype="object")

    def flag(mask, reason):
        mask = mask.fillna(True)
        reasons.loc[mask] = reasons.loc[mask].apply(
            lambda old: f"{old};{reason}" if old else reason
        )

    flag(numeric.isna().any(axis=1), "missing_or_non_numeric")
    flag(numeric.isin([float("inf"), float("-inf")]).any(axis=1),
         "non_finite_values")
    flag((numeric[["open", "high", "low", "close"]] <= 0).any(axis=1),
         "non_positive_price")
    flag(numeric["high"] < numeric[["open", "close"]].max(axis=1),
         "high_below_open_or_close")
    flag(numeric["low"] > numeric[["open", "close"]].min(axis=1),
         "low_above_open_or_close")
    flag(numeric["high"] < numeric["low"], "high_below_low")
    flag(numeric["volume"] < 0, "negative_volume")
    flag(df.duplicated(["symbol", "trading_date"], keep=False),
         "duplicate_symbol_date")

    invalid = reasons.ne("")
    valid_df = df.loc[~invalid].copy()
    quarantine = df.loc[invalid].copy()

    if not quarantine.empty:
        quarantine["validation_reasons"] = reasons.loc[invalid]
        qfile = quarantine_dir / f"{safe_symbol}_{run_id}.parquet"
        qkey = (
            f"quarantine/{base}/provider=yahoo_finance/"
            f"symbol={safe_symbol}/run_id={run_id}/invalid_records.parquet"
        )
        upload(quarantine, qfile, qkey)

    if not valid_df.empty:
        local_file = staging_dir / f"{safe_symbol}.parquet"
        valid_df.to_parquet(
            local_file, engine="pyarrow", compression="zstd", index=False
        )

    print(f"Raw records preserved: {len(df)}")
    print(f"Valid records staged: {len(valid_df)}")
    print(f"Quarantined records: {len(quarantine)}")
    print(f"Bronze: s3://{BUCKET}/{raw_key}")

def run_yahoo(symbols, start, asset_class="EQUITY"):
    for symbol in symbols:
        try:
            df = download_yahoo_history(symbol, start)

            if df is not None:
                save_and_upload(df, symbol, asset_class)

            time.sleep(2)

        except Exception as error:
            print(f"ERROR: {symbol}: {error}")


def main():
    parser = argparse.ArgumentParser(
        description="Quantera Market Data Ingestion"
    )

    parser.add_argument(
        "--provider",
        choices=["yahoo"],
        default="yahoo",
    )

    parser.add_argument(
        "--symbols",
        nargs="+",
        required=True,
    )

    parser.add_argument(
        "--start",
        default=START_DATE,
    )

    parser.add_argument("--asset-class", choices=["EQUITY", "ETF", "INDEX"], default="EQUITY")

    args = parser.parse_args()

    run_yahoo(args.symbols, args.start, args.asset_class)


if __name__ == "__main__":
    main()

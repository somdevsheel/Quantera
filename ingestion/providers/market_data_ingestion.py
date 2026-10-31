
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

STAGING = Path("data/staging/market")
STAGING.mkdir(parents=True, exist_ok=True)


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

    df = df.dropna(subset=required)

    df = df.sort_values("trading_date")

    if df.duplicated(["symbol", "trading_date"]).any():
        raise ValueError(f"Duplicate daily records found for {symbol}")

    if (
        (df["high"] < df[["open", "close"]].max(axis=1)).any()
        or (df["low"] > df[["open", "close"]].min(axis=1)).any()
        or (df["high"] < df["low"]).any()
        or (df["volume"] < 0).any()
    ):
        raise ValueError(f"Invalid OHLCV records for {symbol}")

    return df


def save_and_upload(df, symbol):
    safe_symbol = symbol.replace("^", "").replace(".", "_")

    local_file = STAGING / f"{safe_symbol}.parquet"

    df.to_parquet(
        local_file,
        engine="pyarrow",
        compression="zstd",
        index=False,
    )

    ingestion_date = date.today().isoformat()

    s3_key = (
        f"bronze/market/provider=yahoo_finance/"
        f"symbol={safe_symbol}/"
        f"ingestion_date={ingestion_date}/"
        f"daily_bars.parquet"
    )

    s3 = get_s3_client()

    s3.upload_file(
        str(local_file),
        BUCKET,
        s3_key,
        ExtraArgs={
            "ContentType": "application/vnd.apache.parquet"
        },
    )

    print(f"Local file: {local_file}")
    print(f"S3 location: s3://{BUCKET}/{s3_key}")
    print(f"Records uploaded: {len(df)}")


def run_yahoo(symbols, start):
    for symbol in symbols:
        try:
            df = download_yahoo_history(symbol, start)

            if df is not None:
                save_and_upload(df, symbol)

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

    args = parser.parse_args()

    run_yahoo(args.symbols, args.start)


if __name__ == "__main__":
    main()

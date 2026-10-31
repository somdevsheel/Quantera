from pathlib import Path
from datetime import date

import polars as pl

from registry.ingestion_registry import IngestionRegistry


DATA_DIR = Path("data/staging/market")
BUCKET = "quant-global-data-lake-prod"
PROVIDER = "yahoo_finance"

S3_SYMBOLS = {
    "RELIANCE_NS": "RELIANCE_NS",
    "TCS_NS": "TCS_NS",
}

def register_files():
    registry = IngestionRegistry()

    files = sorted(DATA_DIR.glob("*.parquet"))

    if not files:
        print("No Parquet files found.")
        registry.close()
        return

    for file_path in files:
        try:
            df = pl.read_parquet(file_path)

            if "trading_date" not in df.columns:
                raise ValueError("Missing trading_date column")

            if "symbol" in df.columns and df["symbol"].n_unique() == 1:
                symbol = str(df["symbol"][0])
            else:
                symbol = file_path.stem

            start_date = str(df["trading_date"].min())
            end_date = str(df["trading_date"].max())
            record_count = df.height

            safe_symbol = file_path.stem

            s3_key = (
                f"bronze/market/provider={PROVIDER}/"
                f"symbol={safe_symbol}/"
                f"ingestion_date={date.today().isoformat()}/"
                f"daily_bars.parquet"
            )

            registry.register_ingestion(
                symbol=symbol,
                provider=PROVIDER,
                start_date=start_date,
                end_date=end_date,
                record_count=record_count,
                local_file=str(file_path),
                s3_bucket=BUCKET,
                s3_key=s3_key,
                asset_class="EQUITY",
                status="SUCCESS",
            )

            print(
                f"Processed {symbol}: "
                f"{record_count:,} rows | "
                f"{start_date} to {end_date}"
            )

        except Exception as error:
            print(f"FAILED: {file_path.name}: {error}")

    print("\nFinal Registry Summary:")
    print(registry.get_summary())

    registry.close()


if __name__ == "__main__":
    register_files()

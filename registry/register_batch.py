from pathlib import Path
import pandas as pd
from registry.ingestion_registry import IngestionRegistry

symbols = """
ABBV ABT ACN ADBE AMD AMGN AMT ANET AVGO AXP
BA BAC BKNG BLK BMY CAT COST CRM CSCO CVX
""".split()

registry = IngestionRegistry()
registered = []

for symbol in symbols:
    bronze_files = sorted(
        Path("data/bronze/market").glob(f"{symbol}_*.parquet")
    )
    staging = Path(f"data/staging/market/{symbol}.parquet")

    if not bronze_files or not staging.exists():
        print(f"SKIPPED: {symbol}")
        continue

    bronze = bronze_files[-1]
    run_id = bronze.stem[len(symbol) + 1:]
    df = pd.read_parquet(staging)

    registry.register_ingestion(
        symbol=symbol,
        provider="yahoo_finance",
        start_date=str(pd.to_datetime(df["trading_date"]).min().date()),
        end_date=str(pd.to_datetime(df["trading_date"]).max().date()),
        record_count=len(df),
        local_file=staging,
        s3_bucket="quant-global-data-lake-prod",
        s3_key=(
            f"bronze/market/provider=yahoo_finance/"
            f"symbol={symbol}/run_id={run_id}/daily_bars.parquet"
        ),
        asset_class="EQUITY",
        status="SUCCESS",
    )

    registered.append(symbol)
    print(f"REGISTERED: {symbol}")

print(f"\nRegistered: {len(registered)}/20")
print("Summary:", registry.get_summary())
registry.connection.close()

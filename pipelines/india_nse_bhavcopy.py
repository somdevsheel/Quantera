import zipfile
from pathlib import Path
import pandas as pd

SOURCE = Path("data/staging/india/historical/nse_bhavcopy_20261030.zip")
BRONZE = Path("data/bronze/india/equity_prices")
SILVER = Path("data/silver/india/equity_prices")

BRONZE.mkdir(parents=True, exist_ok=True)
SILVER.mkdir(parents=True, exist_ok=True)

with zipfile.ZipFile(SOURCE) as archive:
    csv_file = next(
        name for name in archive.namelist()
        if name.lower().endswith(".csv")
    )
    with archive.open(csv_file) as f:
        raw = pd.read_csv(f, dtype={"ISIN": str, "TckrSymb": str})

# Keep equity trading instruments, excluding debt and derivatives.
df = raw[
    (raw["FinInstrmTp"] == "STK") &
    (raw["SctyTp"] == "EQ")
].copy() if "SctyTp" in raw.columns else raw[
    (raw["FinInstrmTp"] == "STK") &
    (raw["SctySrs"].isin(["EQ", "BE", "BZ", "SM", "ST"]))
].copy()

numeric_columns = {
    "OpnPric": "open",
    "HghPric": "high",
    "LwPric": "low",
    "ClsPric": "close",
    "LastPric": "last_price",
    "PrvsClsgPric": "previous_close",
    "TtlTradgVol": "volume",
    "TtlTrfVal": "traded_value",
    "TtlNbOfTxsExctd": "trade_count",
}

result = pd.DataFrame({
    "trade_date": pd.to_datetime(df["TradDt"], errors="coerce"),
    "isin": df["ISIN"].astype("string").str.strip(),
    "symbol": df["TckrSymb"].astype("string").str.strip(),
    "security_series": df["SctySrs"].astype("string").str.strip(),
    "exchange": "NSE",
    "exchange_mic": "XNSE",
    "currency": "INR",
})

for source_column, target_column in numeric_columns.items():
    result[target_column] = pd.to_numeric(
        df[source_column], errors="coerce"
    ).values

result["instrument_id"] = (
    "IN:" + result["isin"].fillna("")
)
result.loc[result["isin"].isna() | result["isin"].eq(""), "instrument_id"] = None

# Reject records with invalid dates or missing essential price values.
valid = (
    result["trade_date"].notna() &
    result["symbol"].notna() &
    result["close"].gt(0) &
    result["high"].ge(result["low"]) &
    result["high"].ge(result["open"]) &
    result["low"].le(result["open"])
)

invalid = result[~valid].copy()
result = result[valid].copy()

result["volume"] = result["volume"].fillna(0).astype("int64")
result["trade_count"] = result["trade_count"].fillna(0).astype("int64")

result = result.drop_duplicates(
    ["trade_date", "exchange_mic", "symbol"],
    keep="last"
)

trade_date = result["trade_date"].dt.strftime("%Y%m%d").iloc[0]

bronze_path = BRONZE / f"nse_bhavcopy_{trade_date}.csv"
silver_path = SILVER / f"nse_equity_prices_{trade_date}.parquet"
quarantine_path = SILVER / f"nse_invalid_records_{trade_date}.csv"

with zipfile.ZipFile(SOURCE) as archive:
    archive.extract(csv_file, BRONZE)
    extracted = BRONZE / csv_file
    if extracted != bronze_path:
        extracted.replace(bronze_path)

result.to_parquet(silver_path, index=False)
invalid.to_csv(quarantine_path, index=False)

print("NSE EQUITY PRICE NORMALIZATION")
print("Raw records:", len(raw))
print("Equity candidates:", len(df))
print("Valid records:", len(result))
print("Invalid records:", len(invalid))
print("Duplicate normalized keys:",
      result.duplicated(["trade_date", "exchange_mic", "symbol"]).sum())
print("Trade date:", trade_date)
print("Bronze:", bronze_path)
print("Silver:", silver_path)
print("Quarantine:", quarantine_path)

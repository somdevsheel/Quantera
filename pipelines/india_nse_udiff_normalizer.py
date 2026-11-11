import argparse
import zipfile
from pathlib import Path

import pandas as pd

parser = argparse.ArgumentParser()
parser.add_argument("archive")
args = parser.parse_args()

source = Path(args.archive)
bronze = Path("data/bronze/india/equity_prices")
silver = Path("data/silver/india/equity_prices")
bronze.mkdir(parents=True, exist_ok=True)
silver.mkdir(parents=True, exist_ok=True)

with zipfile.ZipFile(source) as z:
    csv_name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
    with z.open(csv_name) as f:
        raw = pd.read_csv(f, dtype={"ISIN": str, "TckrSymb": str})

raw.columns = raw.columns.str.strip()

df = raw[
    (raw["FinInstrmTp"] == "STK") &
    (raw["SctySrs"].isin(["EQ", "BE", "BZ", "SM", "ST"]))
].copy()

dates = pd.to_datetime(df["TradDt"], errors="coerce").dropna().dt.strftime("%Y%m%d").unique()
if len(dates) != 1:
    raise SystemExit(f"Expected one trade date, found: {dates}")

day = dates[0]
bronze_path = bronze / f"nse_bhavcopy_{day}.csv"
silver_path = silver / f"nse_equity_prices_{day}.parquet"
quarantine_path = silver / f"nse_invalid_records_{day}.csv"

if silver_path.exists() or bronze_path.exists():
    raise SystemExit(f"Output already exists for {day}. No files overwritten.")

numeric = {
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

for source_col, target_col in numeric.items():
    result[target_col] = pd.to_numeric(df[source_col], errors="coerce").values

result["instrument_id"] = "IN:" + result["isin"].fillna("")
result.loc[result["isin"].isna() | result["isin"].eq(""), "instrument_id"] = None

valid = (
    result["trade_date"].notna() &
    result["symbol"].notna() &
    result["symbol"].ne("") &
    result["close"].gt(0) &
    result["high"].ge(result["low"]) &
    result["high"].ge(result["open"]) &
    result["low"].le(result["open"])
)

invalid = result[~valid].copy()
result = result[valid].copy()
result["volume"] = result["volume"].fillna(0).astype("int64")
result["trade_count"] = result["trade_count"].fillna(0).astype("int64")

duplicates = result.duplicated(
    ["trade_date", "exchange_mic", "symbol"]
).sum()
result = result.drop_duplicates(
    ["trade_date", "exchange_mic", "symbol"], keep="last"
)

with zipfile.ZipFile(source) as z:
    z.extract(csv_name, bronze)
    extracted = bronze / csv_name
    extracted.replace(bronze_path)

result.to_parquet(silver_path, index=False)
invalid.to_csv(quarantine_path, index=False)

print("UDiFF NORMALIZATION")
print("Trade date:", day)
print("Raw records:", len(raw))
print("Equity candidates:", len(df))
print("Valid records:", len(result))
print("Invalid records:", len(invalid))
print("Duplicate keys removed:", duplicates)
print("ISIN coverage:", f"{result['isin'].notna().mean():.2%}")
print("Silver:", silver_path)

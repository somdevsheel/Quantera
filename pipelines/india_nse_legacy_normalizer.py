import sys
import zipfile
from pathlib import Path
import pandas as pd

if len(sys.argv) != 2:
    raise SystemExit("Usage: python pipelines/india_nse_legacy_normalizer.py <zip_file>")

source = Path(sys.argv[1])
bronze = Path("data/bronze/india/equity_prices")
silver = Path("data/silver/india/equity_prices")
bronze.mkdir(parents=True, exist_ok=True)
silver.mkdir(parents=True, exist_ok=True)

with zipfile.ZipFile(source) as z:
    name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
    with z.open(name) as f:
        raw = pd.read_csv(f, dtype={"SYMBOL": str, "SERIES": str, "ISIN": str})

raw.columns = raw.columns.str.strip()
raw["SYMBOL"] = raw["SYMBOL"].str.strip()
raw["SERIES"] = raw["SERIES"].str.strip()

df = raw[raw["SERIES"].isin(["EQ", "BE", "BZ", "E1"])].copy()

result = pd.DataFrame({
    "trade_date": pd.to_datetime(df["TIMESTAMP"], format="%d-%b-%Y", errors="coerce"),
    "isin": df["ISIN"].astype("string").str.strip() if "ISIN" in df.columns else pd.Series(pd.NA, index=df.index, dtype="string"),
    "symbol": df["SYMBOL"],
    "security_series": df["SERIES"],
    "exchange": "NSE",
    "exchange_mic": "XNSE",
    "currency": "INR",
})

result["isin"] = result["isin"].replace("", pd.NA)
result["instrument_id"] = "IN:" + result["isin"].fillna("")
result.loc[result["isin"].isna(), "instrument_id"] = None

mapping = {
    "OPEN": "open",
    "HIGH": "high",
    "LOW": "low",
    "CLOSE": "close",
    "LAST": "last_price",
    "PREVCLOSE": "previous_close",
    "TOTTRDQTY": "volume",
    "TOTTRDVAL": "traded_value",
}

for src, dst in mapping.items():
    result[dst] = pd.to_numeric(df[src], errors="coerce").values

trade_col = "TOTALTRADES" if "TOTALTRADES" in df.columns else None
result["trade_count"] = (
    pd.to_numeric(df[trade_col], errors="coerce").values
    if trade_col else pd.NA
)

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

result = result.drop_duplicates(
    ["trade_date", "exchange_mic", "symbol", "security_series"],
    keep="last"
)

if result.empty:
    raise SystemExit("No valid equity records")

date = result["trade_date"].dt.strftime("%Y%m%d").iloc[0]
out = silver / f"nse_legacy_equity_prices_{date}.parquet"
quarantine = silver / f"nse_legacy_invalid_{date}.csv"

if out.exists():
    raise SystemExit(f"Output already exists: {out}")

result.to_parquet(out, index=False)
invalid.to_csv(quarantine, index=False)

print("Trade date:", date)
print("Raw rows:", len(raw))
print("Equity candidates:", len(df))
print("Valid rows:", len(result))
print("Invalid rows:", len(invalid))
print("ISIN coverage:", result["isin"].notna().sum())
print("Trade count coverage:", result["trade_count"].notna().sum())
print("Silver:", out)

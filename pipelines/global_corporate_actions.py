"""Global corporate actions (dividends, splits, capital gains) for the initial
global universes (153 equities, 20 ETFs, 25 indices), extracted from the
Yahoo Finance daily bars already ingested with actions=True — no new requests.

Input : data/staging/{market,etf,indices}/*.parquet
Silver: data/silver/global/corporate_actions/yahoo_corporate_actions.parquet
Gold  : data/gold/global/corporate_actions/yahoo_split_factors.parquet

Semantics (Yahoo): dividends = cash per share in the listing currency on the
ex-date; stock_splits = new/old share ratio (2.0 = 2-for-1; 0.5 = 1-for-2
reverse); capital_gains = fund capital-gain distribution per share.
Split price factor for prices before the ex-date = 1 / ratio.
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {"EQUITY": "data/staging/market", "ETF": "data/staging/etf", "INDEX": "data/staging/indices"}
SILVER = ROOT / "data/silver/global/corporate_actions/yahoo_corporate_actions.parquet"
GOLD = ROOT / "data/gold/global/corporate_actions/yahoo_split_factors.parquet"


def main():
    uni = pd.read_csv(ROOT / "reference/universes/global_equity_universe.csv", dtype=str)
    sym_col = "provider_symbol" if "provider_symbol" in uni else uni.columns[0]
    meta = uni.set_index(sym_col)
    rows = []
    for asset_class, folder in SOURCES.items():
        for f in sorted((ROOT / folder).glob("*.parquet")):
            d = pd.read_parquet(f)
            for col, action in (("dividends", "DIVIDEND"), ("stock_splits", "SPLIT"), ("capital_gains", "CAPITAL_GAIN")):
                if col not in d:
                    continue
                hit = d[pd.to_numeric(d[col], errors="coerce").fillna(0) != 0]
                for r in hit.itertuples():
                    rows.append({"symbol": r.symbol, "asset_class": asset_class,
                                 "ex_date": pd.Timestamp(r.trading_date), "action_type": action,
                                 "value": float(getattr(r, col)), "provider": "yahoo_finance",
                                 "source_file": str(f.relative_to(ROOT))})
    df = pd.DataFrame(rows).sort_values(["symbol", "ex_date", "action_type"])
    for c in ("currency", "country", "exchange_mic", "instrument_id"):
        if c in meta:
            df[c] = df.symbol.map(meta[c])
    df["value_unit"] = df.action_type.map({"DIVIDEND": "cash per share (listing currency)",
                                           "CAPITAL_GAIN": "cash per share (listing currency)",
                                           "SPLIT": "new shares per old share"})
    SILVER.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(SILVER, index=False, compression="zstd")
    s = df[df.action_type == "SPLIT"].copy()
    s["price_factor"] = 1 / s.value
    s["volume_factor"] = s.value
    GOLD.parent.mkdir(parents=True, exist_ok=True)
    s.to_parquet(GOLD, index=False, compression="zstd")
    print("Actions:", len(df), "| symbols:", df.symbol.nunique(), "|", df.ex_date.min().date(), "->", df.ex_date.max().date())
    print(df.groupby(["asset_class", "action_type"]).size().to_string())
    print("Split factors:", len(s))


if __name__ == "__main__":
    main()

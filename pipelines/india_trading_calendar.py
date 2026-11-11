"""India exchange trading calendar (Gold) + NSE published holiday list (Silver).

Holidays : NSE holiday-master API (current year, all segments) -> Bronze snapshot + Silver
Calendar : observed trading days per dataset, derived from Silver files actually held:
             NSE_CM  nse_*equity_prices_YYYYMMDD.parquet
             BSE_CM  bse_bhavcopy_YYYYMMDD.parquet
             NSE_FO  nse_fo_YYYYMMDD.parquet
             NSE_IDX nse_indices_YYYYMMDD.parquet
           A weekday with no file is classified using the logs:
             HOLIDAY_PUBLISHED  listed in NSE holiday master
             NO_SESSION         source reported unavailable (404 / non-data) = holiday or no trading
Outputs  : data/silver/reference/india/nse_holidays.parquet
           data/gold/india/calendar/india_trading_calendar.parquet
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data/bronze/india/calendar"
HOLIDAYS = ROOT / "data/silver/reference/india/nse_holidays.parquet"
GOLD = ROOT / "data/gold/india/calendar/india_trading_calendar.parquet"
PAGE = "https://www.nseindia.com/resources/exchange-communication-holidays"
SOURCES = {
    "NSE_CM": ("data/silver/india/equity_prices", "nse_*equity_prices_*.parquet"),
    "BSE_CM": ("data/silver/india/equity_prices/bse_bhavcopy", "year=*/month=*/bse_bhavcopy_????????.parquet"),
    "NSE_FO": ("data/silver/india/derivatives/nse_fo", "year=*/nse_fo_*.parquet"),
    "NSE_IDX": ("data/silver/india/indices/nse", "year=*/nse_indices_*.parquet"),
}


def fetch_holidays():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
                      "Accept": "application/json", "Referer": PAGE})
    s.get(PAGE, timeout=30)
    r = s.get("https://www.nseindia.com/api/holiday-master", params={"type": "trading"}, timeout=30)
    r.raise_for_status()
    now = datetime.now(timezone.utc)
    BRONZE.mkdir(parents=True, exist_ok=True)
    (BRONZE / f"nse_holiday_master_{now:%Y%m%d}.json").write_bytes(r.content)
    rows = []
    for f in sorted(BRONZE.glob("nse_holiday_master_*.json")):
        for segment, items in json.loads(f.read_text()).items():
            for it in items:
                rows.append({"segment": segment,
                             "holiday_date": pd.to_datetime(it["tradingDate"], format="%d-%b-%Y"),
                             "description": it.get("description"),
                             "source_file": str(f.relative_to(ROOT))})
    h = pd.DataFrame(rows).drop_duplicates(["segment", "holiday_date"], keep="last")
    HOLIDAYS.parent.mkdir(parents=True, exist_ok=True)
    h.to_parquet(HOLIDAYS, index=False)
    return h


def main():
    h = fetch_holidays()
    published = set(h[h.segment == "CM"].holiday_date.dt.date)
    frames = []
    for name, (folder, pattern) in SOURCES.items():
        days = sorted({pd.Timestamp(p.stem[-8:]) for p in (ROOT / folder).glob(pattern)})
        if not days:
            continue
        cal = pd.DataFrame({"date": pd.bdate_range(days[0], days[-1])})
        held = set(days)
        cal["dataset"] = name
        cal["is_trading_day"] = cal.date.isin(held)
        cal["status"] = "TRADED"
        cal.loc[~cal.is_trading_day, "status"] = cal.loc[~cal.is_trading_day, "date"].map(
            lambda d: "HOLIDAY_PUBLISHED" if d.date() in published else "NO_SESSION_OR_MISSING")
        frames.append(cal)
    gold = pd.concat(frames, ignore_index=True)
    GOLD.parent.mkdir(parents=True, exist_ok=True)
    gold.to_parquet(GOLD, index=False)
    print("Holidays:", len(h), "rows,", h.segment.nunique(), "segments")
    print(gold.groupby(["dataset", "status"]).size().unstack(fill_value=0).to_string())


if __name__ == "__main__":
    main()

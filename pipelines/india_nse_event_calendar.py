"""NSE corporate event calendar (board meetings: financial results, dividends,
fund raising, buybacks, ...), incl. announced future meetings.

Source : https://www.nseindia.com/api/event-calendar?index=equities&from_date=&to_date=
Bronze : data/bronze/india/calendar/nse_events/year=YYYY/nse_events_YYYYMM.json
Silver : data/silver/india/calendar/nse_event_calendar.parquet
Log    : data/staging/india/nse_event_calendar_log.csv
Months from the previous month onward are always refreshed (future events change).
event_type is rule-classified from `purpose`; raw purpose/description are kept.
"""
import argparse
import calendar
import csv
import hashlib
import json
import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data/bronze/india/calendar/nse_events"
SILVER = ROOT / "data/silver/india/calendar/nse_event_calendar.parquet"
LOG = ROOT / "data/staging/india/nse_event_calendar_log.csv"
PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-event-calendar"
API = "https://www.nseindia.com/api/event-calendar"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
           "Accept": "application/json", "Referer": PAGE}
RULES = [("FINANCIAL_RESULTS", r"financial result|results"), ("DIVIDEND", r"dividend"),
         ("BONUS", r"bonus"), ("SPLIT", r"split|sub-?division"), ("BUYBACK", r"buy ?back"),
         ("FUND_RAISING", r"fund raising|preferential|qip|rights|debenture|ncd|warrant"),
         ("MERGER_SCHEME", r"amalgamation|merger|scheme|demerger|arrangement"),
         ("DELISTING", r"delist")]


def classify(text):
    t = (text or "").lower()
    hits = [n for n, rx in RULES if re.search(rx, t)]
    return (hits[0] if hits else "OTHER"), "|".join(hits)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2010-01")
    p.add_argument("--end", default=f"{date.today().year}-12")
    p.add_argument("--pause", type=float, default=1.5)
    a = p.parse_args()
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get(PAGE, timeout=30)
    today = date.today()
    refresh_from = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    y, m = map(int, a.start.split("-"))
    ey, em = map(int, a.end.split("-"))
    new = not LOG.exists()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["period", "status", "records", "sha256", "details", "logged_utc"])
        if new:
            w.writeheader()
        while (y, m) <= (ey, em):
            target = BRONZE / f"year={y}" / f"nse_events_{y:04d}{m:02d}.json"
            if not target.exists() or (y, m) >= refresh_from:
                row = {"period": f"{y:04d}{m:02d}", "logged_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                params = {"index": "equities", "from_date": f"01-{m:02d}-{y}",
                          "to_date": f"{calendar.monthrange(y, m)[1]:02d}-{m:02d}-{y}"}
                for attempt in range(1, 4):
                    try:
                        r = s.get(API, params=params, timeout=60)
                        if r.status_code in (401, 403):
                            s.get(PAGE, timeout=30)
                            raise requests.HTTPError(f"HTTP {r.status_code}")
                        r.raise_for_status()
                        data = r.json()
                        if not isinstance(data, list):
                            raise ValueError("unexpected payload")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        tmp = target.with_suffix(".tmp")
                        tmp.write_bytes(r.content)
                        os.replace(tmp, target)
                        row.update(status="DOWNLOADED", records=len(data), sha256=hashlib.sha256(r.content).hexdigest())
                        break
                    except (requests.RequestException, ValueError) as exc:
                        row.update(status="FAILED", details=str(exc)[:200])
                        time.sleep(5 * attempt)
                w.writerow(row)
                fh.flush()
                print(row["period"], row["status"], row.get("records", ""), flush=True)
                time.sleep(a.pause)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    recs = []
    for f in sorted(BRONZE.glob("year=*/nse_events_*.json")):
        for r in json.loads(f.read_text()):
            r["_src"] = str(f.relative_to(ROOT))
            recs.append(r)
    df = pd.DataFrame(recs)
    out = pd.DataFrame({
        "event_date": pd.to_datetime(df["date"], format="%d-%b-%Y", errors="coerce"),
        "symbol": df["symbol"].astype("string"),
        "company_name": df["company"].astype("string"),
        "purpose": df["purpose"].astype("string"),
        "description": df["bm_desc"].astype("string"),
        "source_file": df["_src"].astype("string"),
    })
    cls = (out.purpose.fillna("") + " " + out.description.fillna("")).map(classify)
    out["event_type"] = [c[0] for c in cls]
    out["event_flags"] = [c[1] for c in cls]
    out["is_future"] = out.event_date > pd.Timestamp(today)
    out["exchange"] = "NSE"
    out = out.drop_duplicates(subset=["event_date", "symbol", "purpose", "description"])
    SILVER.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(SILVER, index=False, compression="zstd")
    print("Events:", len(out), "|", out.event_date.min(), "->", out.event_date.max())
    print(out.event_type.value_counts().to_string())


if __name__ == "__main__":
    main()

"""NSE index daily close (all NIFTY indices incl. India VIX): Bronze + Silver.

Source : https://nsearchives.nseindia.com/content/indices/ind_close_all_DDMMYYYY.csv
         One file per trading day, ~100-170 indices. Published from 2012;
         earlier history is a documented gap (needs niftyindices.com history).
Bronze : data/bronze/india/indices/nse/year=YYYY/ind_close_all_DDMMYYYY.csv
Silver : data/silver/india/indices/nse/year=YYYY/nse_indices_YYYYMMDD.parquet
Log    : data/staging/india/nse_indices_log.csv
Restartable; 404 = non-trading day / unpublished, logged as UNAVAILABLE.
"""
import argparse
import csv
import hashlib
import io
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data/bronze/india/indices/nse"
SILVER = ROOT / "data/silver/india/indices/nse"
LOG = ROOT / "data/staging/india/nse_indices_log.csv"
BASE = "https://nsearchives.nseindia.com/content/indices/"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com/"}
LOG_FIELDS = ["trade_date", "download_status", "http_status", "bytes", "sha256",
              "silver_status", "rows", "details", "logged_utc"]
SCHEMA_VERSION = "nse_indices_v1"

COLUMNS = {
    "Index Name": "index_name", "Index Date": "source_date",
    "Open Index Value": "open", "High Index Value": "high",
    "Low Index Value": "low", "Closing Index Value": "close",
    "Points Change": "points_change", "Change(%)": "change_pct",
    "Volume": "volume", "Turnover (Rs. Cr.)": "turnover_inr_crore",
    "P/E": "pe", "P/B": "pb", "Div Yield": "dividend_yield",
}


def index_id(name):
    return "NSEIDX:" + re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")


def download(day, session, retries):
    name = f"ind_close_all_{day:%d%m%Y}.csv"
    target = BRONZE / f"year={day.year}" / name
    if target.exists() and target.read_bytes()[:10] == b"Index Name":
        return target, "EXISTING", "", ""
    last = ""
    for attempt in range(1, retries + 1):
        try:
            r = session.get(BASE + name, timeout=(10, 45))
            if r.status_code == 404:
                return None, "UNAVAILABLE", 404, "HTTP 404"
            r.raise_for_status()
            if r.content[:10] != b"Index Name":
                return None, "UNAVAILABLE", r.status_code, "non-data response"
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".csv.tmp")
            tmp.write_bytes(r.content)
            os.replace(tmp, target)
            return target, "DOWNLOADED", r.status_code, f"attempt {attempt}"
        except requests.RequestException as exc:
            last = str(exc)[:300]
            time.sleep(3 * 2 ** (attempt - 1))
    return None, "FAILED", "", last


def normalize(day, path):
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns=COLUMNS)[[c for c in COLUMNS.values() if c in df.rename(columns=COLUMNS)]]
    for c in df.columns:
        if c not in ("index_name", "source_date"):
            df[c] = pd.to_numeric(df[c].replace("-", pd.NA), errors="coerce").astype("float64")
    df["index_name"] = df.index_name.astype(str).str.strip()
    df = df.drop_duplicates("index_name", keep="last")
    df.insert(0, "trade_date", pd.Timestamp(day))
    df.insert(1, "index_id", df.index_name.map(index_id))
    df["exchange"] = "NSE"
    df["currency"] = "INR"
    df["source_file"] = str(path.relative_to(ROOT))
    df["schema_version"] = SCHEMA_VERSION
    out = SILVER / f"year={day.year}" / f"nse_indices_{day:%Y%m%d}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, out)
    return len(df)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--pause", type=float, default=0.5)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--renormalize", action="store_true")
    a = p.parse_args()
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)

    s = requests.Session()
    s.headers.update(HEADERS)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    new = not LOG.exists()
    counts = {}
    with LOG.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        day = start
        while day <= end:
            out = SILVER / f"year={day.year}" / f"nse_indices_{day:%Y%m%d}.parquet"
            if day.weekday() >= 5 or (out.exists() and not a.renormalize):
                day += timedelta(days=1)
                continue
            path, status, http, details = download(day, s, a.retries)
            row = {"trade_date": day.isoformat(), "download_status": status, "http_status": http,
                   "details": details, "logged_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            if path:
                b = path.read_bytes()
                row.update(bytes=len(b), sha256=hashlib.sha256(b).hexdigest())
                try:
                    row.update(silver_status="NORMALIZED", rows=normalize(day, path))
                except Exception as exc:  # noqa: BLE001
                    row.update(silver_status="FAILED", details=f"normalize: {exc}"[:300])
            else:
                row["silver_status"] = "NOT_RUN"
            w.writerow(row)
            fh.flush()
            k = f"{status}/{row['silver_status']}"
            counts[k] = counts.get(k, 0) + 1
            print(day, status, row["silver_status"], row.get("rows", ""), flush=True)
            if status != "EXISTING":
                time.sleep(a.pause)
            day += timedelta(days=1)
    print("\nNSE INDICES SUMMARY")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()

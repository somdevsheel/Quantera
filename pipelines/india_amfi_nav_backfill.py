"""AMFI mutual-fund NAV history (all Indian MF schemes, incl. ETFs reported to AMFI).

Source : https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?frmdt=&todt=
         Semicolon-delimited text with section headers:
           "Open Ended Schemes ( Equity Scheme - Large Cap Fund )"  -> category
           "<AMC name>"                                            -> fund house
         Published from April 2006.
Bronze : data/bronze/india/mutual_funds/amfi_nav/year=YYYY/amfi_nav_YYYYMM.txt.gz
Silver : data/silver/india/mutual_funds/nav/year=YYYY/amfi_nav_YYYYMM.parquet
Log    : data/staging/india/amfi_nav_log.csv
One request per calendar month; restartable. The current (incomplete) month
is always re-downloaded unless --no-refresh-current.
"""
import argparse
import calendar
import csv
import gzip
import hashlib
import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data/bronze/india/mutual_funds/amfi_nav"
SILVER = ROOT / "data/silver/india/mutual_funds/nav"
LOG = ROOT / "data/staging/india/amfi_nav_log.csv"
URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
HEADER = "Scheme Code;"
SCHEMA_VERSION = "amfi_nav_v1"
LOG_FIELDS = ["period", "download_status", "bytes", "sha256", "silver_status",
              "rows", "schemes", "details", "logged_utc"]
CATEGORY_RE = re.compile(r"^(Open Ended|Close Ended|Interval Fund)\s*Schemes?\s*\((.*)\)\s*$", re.I)


def parse(text):
    rows, category, scheme_type, amc = [], None, None, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(HEADER):
            continue
        m = CATEGORY_RE.match(line)
        if m:
            scheme_type, category = m.group(1).title(), m.group(2).strip()
            continue
        if ";" not in line:
            amc = re.sub(r"\s+", " ", line)
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 8:
            continue
        rows.append(parts[:8] + [scheme_type, category, amc])
    df = pd.DataFrame(rows, columns=[
        "scheme_code", "scheme_name", "plan", "option", "isin_growth_or_payout",
        "isin_reinvestment", "nav_raw", "nav_date_raw", "scheme_type",
        "scheme_category", "amc"])
    df["nav"] = pd.to_numeric(df.nav_raw, errors="coerce")
    df["nav_date"] = pd.to_datetime(df.nav_date_raw, format="%d-%b-%Y", errors="coerce")
    for c in ["plan", "option", "isin_growth_or_payout", "isin_reinvestment"]:
        df[c] = df[c].replace({"": pd.NA, "-": pd.NA})
    df["quality_flag"] = pd.NA
    df.loc[df.nav.isna(), "quality_flag"] = "NAV_NOT_NUMERIC"
    df.loc[df.nav_date.isna(), "quality_flag"] = "DATE_UNPARSEABLE"
    df = df.drop_duplicates(subset=["scheme_code", "nav_date"], keep="last")
    df["currency"] = "INR"
    df["source"] = "AMFI"
    df["schema_version"] = SCHEMA_VERSION
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].astype("string")
    return df


def months(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", required=True, help="YYYY-MM")
    p.add_argument("--end", required=True, help="YYYY-MM")
    p.add_argument("--pause", type=float, default=2.0)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--no-refresh-current", action="store_true")
    a = p.parse_args()
    start = date.fromisoformat(a.start + "-01")
    end = date.fromisoformat(a.end + "-01")
    today = date.today()

    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    LOG.parent.mkdir(parents=True, exist_ok=True)
    new = not LOG.exists()
    with LOG.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        for y, m in months(start, end):
            period = f"{y:04d}{m:02d}"
            bronze = BRONZE / f"year={y}" / f"amfi_nav_{period}.txt.gz"
            silver = SILVER / f"year={y}" / f"amfi_nav_{period}.parquet"
            current = (y, m) == (today.year, today.month) and not a.no_refresh_current
            if silver.exists() and not current:
                continue
            row = {"period": period, "logged_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            content = None
            if bronze.exists() and not current:
                content = gzip.decompress(bronze.read_bytes())
                row["download_status"] = "EXISTING"
            else:
                last_day = min(date(y, m, calendar.monthrange(y, m)[1]), today)
                params = {"frmdt": date(y, m, 1).strftime("%d-%b-%Y"), "todt": last_day.strftime("%d-%b-%Y")}
                for attempt in range(1, a.retries + 1):
                    try:
                        r = s.get(URL, params=params, timeout=(15, 300))
                        r.raise_for_status()
                        if not r.text.lstrip().startswith(HEADER):
                            raise ValueError("unexpected payload")
                        content = r.content
                        break
                    except (requests.RequestException, ValueError) as exc:
                        row["details"] = str(exc)[:300]
                        time.sleep(5 * 2 ** (attempt - 1))
                if content is None:
                    row.update(download_status="FAILED", silver_status="NOT_RUN")
                    w.writerow(row); fh.flush()
                    print(period, "FAILED", row.get("details"), flush=True)
                    continue
                bronze.parent.mkdir(parents=True, exist_ok=True)
                tmp = bronze.with_suffix(".gz.tmp")
                tmp.write_bytes(gzip.compress(content, mtime=0))
                os.replace(tmp, bronze)
                row["download_status"] = "DOWNLOADED"
                time.sleep(a.pause)
            row["bytes"] = len(content)
            row["sha256"] = hashlib.sha256(content).hexdigest()
            try:
                df = parse(content.decode("utf-8", errors="replace"))
                df["source_file"] = str(bronze.relative_to(ROOT))
                silver.parent.mkdir(parents=True, exist_ok=True)
                tmp = silver.with_suffix(".parquet.tmp")
                df.to_parquet(tmp, index=False, compression="zstd")
                os.replace(tmp, silver)
                row.update(silver_status="NORMALIZED", rows=len(df), schemes=df.scheme_code.nunique())
            except Exception as exc:  # noqa: BLE001
                row.update(silver_status="FAILED", details=f"parse: {exc}"[:300])
            w.writerow(row); fh.flush()
            print(period, row["download_status"], row["silver_status"], row.get("rows", ""), row.get("schemes", ""), flush=True)


if __name__ == "__main__":
    main()

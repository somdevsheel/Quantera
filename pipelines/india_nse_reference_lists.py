"""NSE public reference lists: symbol changes, index constituents (with industry), equity list.

Source : https://nsearchives.nseindia.com/content/equities/symbolchange.csv
         https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv
         https://nsearchives.nseindia.com/content/indices/ind_<index>list.csv   (see INDEX_FILES)
         Requests identify this client honestly (no browser user-agent); if NSE refuses, the
         file is logged FAILED and nothing is worked around.
Bronze : data/bronze/india/reference/nse/retrieved=YYYYMMDD/<file>           (immutable, per run)
Silver : data/silver/india/reference/nse_symbol_changes.parquet               (all runs, deduplicated)
         data/silver/india/reference/nse_index_constituents.parquet           (one snapshot per run)
         data/silver/india/reference/nse_industry_classification.parquet      (latest industry per ISIN)
Ledger : data/validation/india/nse_reference_lists_ledger.csv

Point in time: constituent lists are snapshots as of `snapshot_date` (the retrieval date). NSE
does not publish historical membership as data, so membership before the first snapshot is
unknown - consumers must not back-fill today's members into the past (survivorship bias).
Industry comes from the index lists (NSE's industry classification), so it covers index
members only (about 750 stocks in Nifty Total Market); other stocks have no industry here.
"""
import argparse
import csv
import hashlib
import io
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data/bronze/india/reference/nse"
SILVER = ROOT / "data/silver/india/reference"
LEDGER = ROOT / "data/validation/india/nse_reference_lists_ledger.csv"
HEADERS = {"User-Agent": "QuanteraDataLake/1.0 (research data pipeline)"}
SCHEMA_VERSION = "nse_reference_lists_v1"
EQUITY_BASE = "https://nsearchives.nseindia.com/content/equities/"
INDEX_BASE = "https://nsearchives.nseindia.com/content/indices/"
INDEX_FILES = {  # index name -> file
    "NIFTY 50": "ind_nifty50list.csv",
    "NIFTY NEXT 50": "ind_niftynext50list.csv",
    "NIFTY 100": "ind_nifty100list.csv",
    "NIFTY 200": "ind_nifty200list.csv",
    "NIFTY 500": "ind_nifty500list.csv",
    "NIFTY MIDCAP 150": "ind_niftymidcap150list.csv",
    "NIFTY SMALLCAP 250": "ind_niftysmallcap250list.csv",
    "NIFTY MICROCAP 250": "ind_niftymicrocap250_list.csv",
    "NIFTY TOTAL MARKET": "ind_niftytotalmarket_list.csv",
    "NIFTY BANK": "ind_niftybanklist.csv",
    "NIFTY IT": "ind_niftyitlist.csv",
}


def download(day, pause):
    out = BRONZE / f"retrieved={day:%Y%m%d}"
    out.mkdir(parents=True, exist_ok=True)
    targets = {"symbolchange.csv": EQUITY_BASE + "symbolchange.csv", "EQUITY_L.csv": EQUITY_BASE + "EQUITY_L.csv"}
    targets.update({f: INDEX_BASE + f for f in INDEX_FILES.values()})
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    new = not LEDGER.exists()
    s = requests.Session()
    s.headers.update(HEADERS)
    with LEDGER.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["retrieved", "file", "status", "bytes", "sha256", "details", "logged_utc"])
        if new:
            w.writeheader()
        for name, url in targets.items():
            row = {"retrieved": f"{day:%Y%m%d}", "file": name,
                   "logged_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            target = out / name
            if target.exists():
                row.update(status="EXISTS", bytes=target.stat().st_size)
            else:
                try:
                    r = s.get(url, timeout=60)
                    r.raise_for_status()
                    if not r.content.strip() or b"<html" in r.content[:200].lower():
                        raise ValueError("not a CSV payload")
                    tmp = target.with_suffix(".tmp")
                    tmp.write_bytes(r.content)
                    tmp.replace(target)
                    row.update(status="DOWNLOADED", bytes=len(r.content), sha256=hashlib.sha256(r.content).hexdigest())
                except (requests.RequestException, ValueError) as exc:
                    row.update(status="FAILED", details=str(exc)[:200])
                time.sleep(pause)
            w.writerow(row)
            print(name, row["status"], row.get("bytes", ""), flush=True)


def _read(path):
    df = pd.read_csv(io.StringIO(path.read_text(encoding="utf-8", errors="replace")), dtype=str)
    df.columns = [c.strip() for c in df.columns]
    return df.apply(lambda c: c.str.strip() if c.dtype == object else c)


def build():
    runs = sorted(BRONZE.glob("retrieved=*"))
    if not runs:
        raise SystemExit("no Bronze runs; run without --build-only first")
    SILVER.mkdir(parents=True, exist_ok=True)

    # Symbol changes: union of all runs.
    sc = []
    for run in runs:
        f = run / "symbolchange.csv"
        if f.exists():
            d = _read(f)
            d.columns = ["company_name", "old_symbol", "new_symbol", "effective_date"]
            d["source_file"] = str(f.relative_to(ROOT))
            sc.append(d)
    sc = pd.concat(sc, ignore_index=True)
    sc["effective_date"] = pd.to_datetime(sc.effective_date, format="%d-%b-%Y", errors="coerce").dt.date
    bad = sc.effective_date.isna() | sc.old_symbol.isna() | sc.new_symbol.isna()
    sc["quality_flag"] = pd.array(["UNPARSED_ROW" if b else None for b in bad], dtype="string")
    sc = sc.drop_duplicates(["old_symbol", "new_symbol", "effective_date"], keep="last")
    sc["schema_version"] = SCHEMA_VERSION
    sc.to_parquet(SILVER / "nse_symbol_changes.parquet", index=False, compression="zstd")

    # Index constituents: one snapshot per run and index.
    cons = []
    for run in runs:
        snap = datetime.strptime(run.name.split("=")[1], "%Y%m%d").date()
        for index_name, fname in INDEX_FILES.items():
            f = run / fname
            if not f.exists():
                continue
            d = _read(f).rename(columns={"Company Name": "company_name", "Industry": "industry", "Symbol": "symbol",
                                         "Series": "series", "ISIN Code": "isin"})
            d["index_name"] = index_name
            d["snapshot_date"] = snap
            d["source_file"] = str(f.relative_to(ROOT))
            cons.append(d[["snapshot_date", "index_name", "symbol", "isin", "series", "company_name", "industry",
                           "source_file"]])
    cons = pd.concat(cons, ignore_index=True)
    cons["instrument_id"] = "IN:" + cons["isin"]
    cons["schema_version"] = SCHEMA_VERSION
    cons.to_parquet(SILVER / "nse_index_constituents.parquet", index=False, compression="zstd")

    # Industry: latest snapshot that classifies the ISIN.
    ind = (cons.dropna(subset=["industry", "isin"]).sort_values("snapshot_date")
           .drop_duplicates("isin", keep="last")[["instrument_id", "isin", "symbol", "company_name", "industry",
                                                  "snapshot_date", "source_file"]])
    ind["classification"] = "NSE index-list industry"
    ind["schema_version"] = SCHEMA_VERSION
    ind.to_parquet(SILVER / "nse_industry_classification.parquet", index=False, compression="zstd")

    print("symbol changes:", len(sc), "| unparsed:", int(bad.sum()))
    print("constituent rows:", len(cons), cons.groupby(["snapshot_date", "index_name"]).size().to_dict())
    print("industry-classified ISINs:", len(ind), "| industries:", ind.industry.nunique())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--pause", type=float, default=2.0)
    a = ap.parse_args()
    if not a.build_only:
        download(date.today(), a.pause)
    build()


if __name__ == "__main__":
    main()

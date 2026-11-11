"""FX / rates / macro / commodity / crypto series from FRED (India by default;
--catalog reference/series/global_fred_series.csv --region global for global).
Originally: India FX / rates / macro series from FRED (public CSV endpoint, no API key).

Catalog: reference/series/india_fred_series.csv (series_id, dataset, units, frequency, ...)
Bronze : data/bronze/india/fred/retrieved=YYYYMMDD/<SERIES>.csv   (dated snapshot = vintage proxy)
Silver : data/silver/india/{fx,rates,macro}/fred_series.parquet   (long format, latest snapshot)
         data/silver/india/fx/inr_reference_rates.parquet         (INR pairs; crosses via USD flagged DERIVED)
Log    : data/staging/india/fred_series_log.csv

fredgraph.csv returns only the latest vintage; release dates / revisions
are not available without an ALFRED API key (documented limitation).
Note: FRED rejects browser-like User-Agents on this endpoint.
"""
import csv
import hashlib
import io
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "reference/series/india_fred_series.csv"
BRONZE = ROOT / "data/bronze/india/fred"
SILVER = ROOT / "data/silver/india"
LOG = ROOT / "data/staging/india/fred_series_log.csv"
URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
SCHEMA_VERSION = "fred_series_v1"

# pair -> (formula, inputs); all expressed as INR per 1 unit of base currency
CROSSES = {
    "USDINR": ("DIRECT", ["DEXINUS"], lambda d: d["DEXINUS"]),
    "EURINR": ("CROSS_VIA_USD", ["DEXINUS", "DEXUSEU"], lambda d: d["DEXINUS"] * d["DEXUSEU"]),
    "GBPINR": ("CROSS_VIA_USD", ["DEXINUS", "DEXUSUK"], lambda d: d["DEXINUS"] * d["DEXUSUK"]),
    "JPYINR": ("CROSS_VIA_USD", ["DEXINUS", "DEXJPUS"], lambda d: d["DEXINUS"] / d["DEXJPUS"]),
    "CNYINR": ("CROSS_VIA_USD", ["DEXINUS", "DEXCHUS"], lambda d: d["DEXINUS"] / d["DEXCHUS"]),
    "SGDINR": ("CROSS_VIA_USD", ["DEXINUS", "DEXSIUS"], lambda d: d["DEXINUS"] / d["DEXSIUS"]),
}


def main():
    import argparse
    global BRONZE, SILVER, LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default=str(CATALOG))
    ap.add_argument("--region", default="india", help="lake region folder: india | global")
    args = ap.parse_args()
    BRONZE = ROOT / f"data/bronze/{args.region}/fred"
    SILVER = ROOT / f"data/silver/{args.region}"
    LOG = ROOT / f"data/staging/{args.region}/fred_series_log.csv"
    catalog = pd.read_csv(args.catalog)
    now = datetime.now(timezone.utc)
    snap = BRONZE / f"retrieved={now:%Y%m%d}"
    snap.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers["User-Agent"] = "python-requests/2.32"
    frames, logrows = [], []

    for meta in catalog.itertuples():
        path = snap / f"{meta.series_id}.csv"
        status, detail = "EXISTING", ""
        if not path.exists():
            status = "FAILED"
            for attempt in range(1, 4):
                try:
                    r = s.get(URL.format(meta.series_id), timeout=(10, 90))
                    r.raise_for_status()
                    if not r.text.startswith("observation_date"):
                        raise ValueError("unexpected payload")
                    tmp = path.with_suffix(".tmp")
                    tmp.write_bytes(r.content)
                    os.replace(tmp, path)
                    status = "DOWNLOADED"
                    break
                except (requests.RequestException, ValueError) as exc:
                    detail = str(exc)[:200]
                    time.sleep(3 * attempt)
            time.sleep(0.5)
        row = {"series_id": meta.series_id, "retrieved": f"{now:%Y-%m-%d}", "status": status, "details": detail}
        if path.exists():
            content = path.read_bytes()
            d = pd.read_csv(io.BytesIO(content))
            d.columns = ["observation_date", "value"]
            d["value"] = pd.to_numeric(d.value, errors="coerce")  # FRED uses "." for missing
            d = d.dropna(subset=["value"])
            d["observation_date"] = pd.to_datetime(d.observation_date)
            for c in ["series_id", "dataset", "title", "units", "frequency", "original_source"]:
                d[c] = getattr(meta, c)
            d["source"] = "FRED"
            d["retrieved_utc"] = now.isoformat(timespec="seconds")
            d["source_file"] = str(path.relative_to(ROOT))
            d["schema_version"] = SCHEMA_VERSION
            frames.append(d)
            row.update(sha256=hashlib.sha256(content).hexdigest(), rows=len(d),
                       first=d.observation_date.min().date(), last=d.observation_date.max().date())
        logrows.append(row)
        print(meta.series_id, status, row.get("rows", ""), row.get("first", ""), row.get("last", ""), flush=True)

    allobs = pd.concat(frames, ignore_index=True)
    for ds, part in allobs.groupby("dataset"):
        out = SILVER / ds / "fred_series.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        part.to_parquet(out, index=False, compression="zstd")

    wide = allobs[allobs.frequency == "daily"].pivot_table(
        index="observation_date", columns="series_id", values="value")
    pairs = []
    for pair, (method, inputs, fn) in CROSSES.items():
        if not set(inputs) <= set(wide.columns):
            continue
        v = fn(wide[inputs]).dropna()
        pairs.append(pd.DataFrame({
            "observation_date": v.index, "pair": pair, "base_currency": pair[:3],
            "quote_currency": "INR", "rate": v.values, "method": method,
            "input_series": "+".join(inputs), "source": "FRED (Federal Reserve H.10)",
            "schema_version": SCHEMA_VERSION}))
    fx = pd.concat(pairs, ignore_index=True) if pairs else pd.DataFrame()
    if not fx.empty:
        fx.to_parquet(SILVER / "fx" / "inr_reference_rates.parquet", index=False, compression="zstd")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    new = not LOG.exists()
    fields = ["series_id", "retrieved", "status", "rows", "first", "last", "sha256", "details"]
    with LOG.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(logrows)
    print("\nSeries:", len(frames), "/", len(catalog), "| observations:", len(allobs))
    if not fx.empty:
        print("INR pairs:", fx.groupby("pair").observation_date.agg(["min", "max", "count"]).to_string())


if __name__ == "__main__":
    main()

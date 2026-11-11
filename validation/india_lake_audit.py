"""India data-lake audit: file manifest, coverage report, data-quality tests.

Outputs (data/validation/india/):
  file_manifest.csv     every Bronze/Silver/Gold file: dataset, layer, path, bytes, sha256
  coverage_report.csv   per dataset: first/last date, files, rows, distinct keys, missing weekdays
  quality_report.csv    per dataset test: name, result (PASS/FAIL/WARN), detail
  audit_summary.json

Run:  python validation/india_lake_audit.py [--no-checksums]
Checksums are cached by (path, size, mtime) in file_manifest.csv so reruns are fast.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/validation/india"
MANIFEST = OUT / "file_manifest.csv"

# dataset -> (silver glob, date column, unique key columns, bronze glob)
DATASETS = {
    "nse_equity_daily": ("data/silver/india/equity_prices/nse_*equity_prices_*.parquet",
                         "trade_date", ["trade_date", "symbol", "security_series"],
                         "data/staging/india/historical/*"),
    "bse_equity_daily_bhavcopy": ("data/silver/india/equity_prices/bse_bhavcopy/year=*/month=*/bse_bhavcopy_????????.parquet",
                                  "trade_date", ["trade_date", "bse_scrip_code"],
                                  "data/bronze/india/equity_prices/bse_bhavcopy/year=*/*"),
    "bse_equity_daily_api": ("data/silver/india/equity_prices/bse/year=*/month=*/*.parquet",
                             "trade_date", ["trade_date", "bse_scrip_code", "demat_flag"],
                             "data/bronze/india/equity_prices/bse/year=*/month=*/*"),
    "nse_indices_daily": ("data/silver/india/indices/nse/year=*/*.parquet",
                          "trade_date", ["trade_date", "index_id"],
                          "data/bronze/india/indices/nse/year=*/*"),
    "nse_fo_daily": ("data/silver/india/derivatives/nse_fo/year=*/*.parquet",
                     "trade_date", ["trade_date", "contract_id"],
                     "data/bronze/india/derivatives/nse_fo/year=*/*"),
    "amfi_mf_nav": ("data/silver/india/mutual_funds/nav/year=*/*.parquet",
                    "nav_date", ["scheme_code", "nav_date"],
                    "data/bronze/india/mutual_funds/amfi_nav/year=*/*"),
    "nse_corporate_actions": ("data/silver/india/corporate_actions/nse_corporate_actions.parquet",
                              "ex_date", ["symbol", "isin", "ex_date", "subject"],
                              "data/bronze/india/corporate_actions/nse/year=*/*"),
    "fred_fx": ("data/silver/india/fx/fred_series.parquet", "observation_date",
                ["series_id", "observation_date"], "data/bronze/india/fred/retrieved=*/*"),
    "fred_rates": ("data/silver/india/rates/fred_series.parquet", "observation_date",
                   ["series_id", "observation_date"], None),
    "fred_macro": ("data/silver/india/macro/fred_series.parquet", "observation_date",
                   ["series_id", "observation_date"], None),
    "inr_fx_pairs": ("data/silver/india/fx/inr_reference_rates.parquet", "observation_date",
                     ["pair", "observation_date"], None),
    "nse_event_calendar": ("data/silver/india/calendar/nse_event_calendar.parquet", "event_date",
                           ["event_date", "symbol", "purpose", "description"],
                           "data/bronze/india/calendar/nse_events/year=*/*"),
    "nse_key_financials": ("data/gold/india/fundamentals/nse_key_financials.parquet", "to_date",
                           ["symbol", "period_type", "from_date", "to_date", "consolidated", "source_file"],
                           "data/bronze/india/fundamentals/nse_results/xbrl/*/*.xml"),
    "nse_xbrl_facts": ("data/silver/india/fundamentals/xbrl_facts/symbol=*/*.parquet", "period_end",
                       ["source_file", "concept", "context_id", "unit", "raw_value"],
                       "data/bronze/india/fundamentals/nse_results/meta/*.json"),
    "nse_option_greeks": ("data/gold/india/derivatives/nse_option_greeks/year=*/nse_option_greeks_????????.parquet",
                          "trade_date", ["trade_date", "contract_id"], None),
}
GOLD = ["data/gold/india/corporate_actions/*.parquet", "data/gold/india/calendar/*.parquet"]
# Duplicates that exist in the source itself and cannot be resolved (kept, reported as WARN).
# 500271 Jan-2000: the BSE API returns two unlabeled rows on 6 days (one large-volume, one tiny;
# most likely demat vs physical segments). No flag distinguishes them and bhavcopy starts 2006.
KNOWN_SOURCE_DUPLICATES = {
    "bse_equity_daily_api": "bse_scrip_code = '500271' AND trade_date BETWEEN '2000-01-01' AND '2000-01-31'",
}
DAILY_FILE_DATASETS = {"nse_equity_daily", "bse_equity_daily_bhavcopy", "nse_indices_daily", "nse_fo_daily", "nse_option_greeks"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(checksums):
    cache = {}
    if MANIFEST.exists():
        old = pd.read_csv(MANIFEST)
        cache = {(r.path, r.bytes, r.mtime): r.sha256 for r in old.itertuples()}
    rows = []
    def add(dataset, layer, pattern):
        for p in sorted(ROOT.glob(pattern)):
            if not p.is_file() or p.suffix == ".tmp":
                continue
            st = p.stat()
            rel = str(p.relative_to(ROOT))
            key = (rel, st.st_size, int(st.st_mtime))
            digest = cache.get(key) or (sha256(p) if checksums else None)
            rows.append({"dataset": dataset, "layer": layer, "path": rel, "bytes": st.st_size,
                         "mtime": int(st.st_mtime), "sha256": digest})
    for name, (silver, _, _, bronze) in DATASETS.items():
        add(name, "silver", silver)
        if bronze:
            add(name, "bronze", bronze)
    for g in GOLD:
        add("gold", "gold", g)
    m = pd.DataFrame(rows).drop_duplicates("path")
    OUT.mkdir(parents=True, exist_ok=True)
    m.to_csv(MANIFEST, index=False)
    return m


def audit(manifest):
    con = duckdb.connect()
    coverage, quality = [], []

    def q(name, test, ok, detail, warn=False):
        quality.append({"dataset": name, "test": test,
                        "result": "PASS" if ok else ("WARN" if warn else "FAIL"), "detail": detail})

    for name, (silver, dcol, key, _) in DATASETS.items():
        files = manifest[(manifest.dataset == name) & (manifest.layer == "silver")]
        if files.empty:
            coverage.append({"dataset": name, "status": "MISSING"})
            q(name, "silver_present", False, "no silver files")
            continue
        glob = str(ROOT / silver)
        src = f"read_parquet('{glob}', union_by_name=true)"
        k = ", ".join(key)
        # Facts whose context the filer never defined are flagged, not dated (by design).
        null_filter = " AND period_source IS DISTINCT FROM 'UNRESOLVED_CONTEXT'" if name == "nse_xbrl_facts" else ""
        stats = con.execute(f"""
            SELECT min({dcol}), max({dcol}), count(*), count(DISTINCT {dcol}),
                   count(*) FILTER (WHERE {dcol} IS NULL{null_filter})
            FROM {src}""").fetchone()
        # XBRL consistent duplicates are kept in Silver but flagged; uniqueness applies to the rest.
        dsrc = f"(SELECT * FROM {src} WHERE NOT coalesce(is_duplicate_fact, false))" if name == "nse_xbrl_facts" else src
        dup = con.execute(f"SELECT count(*) FROM (SELECT {k} FROM {dsrc} GROUP BY {k} HAVING count(*) > 1)").fetchone()[0]
        row = {"dataset": name, "status": "PRESENT", "first_date": str(stats[0])[:10], "last_date": str(stats[1])[:10],
               "rows": stats[2], "distinct_dates": stats[3], "silver_files": len(files),
               "bronze_files": int(((manifest.dataset == name) & (manifest.layer == "bronze")).sum()),
               "silver_bytes": int(files.bytes.sum())}
        if name in DAILY_FILE_DATASETS:
            weekdays = pd.bdate_range(stats[0], stats[1])
            row["weekdays_in_range"] = len(weekdays)
            row["weekdays_without_data"] = len(weekdays) - stats[3]
        coverage.append(row)
        known = KNOWN_SOURCE_DUPLICATES.get(name)
        if known:
            kdup = con.execute(f"SELECT count(*) FROM (SELECT {k} FROM {src} WHERE {known} GROUP BY {k} HAVING count(*) > 1)").fetchone()[0]
            q(name, "unique_key", dup - kdup == 0, f"{dup - kdup} duplicate keys on ({k}) outside known source issues")
            if kdup:
                q(name, "known_source_duplicates", False, f"{kdup} documented source duplicates kept ({known})", warn=True)
        else:
            q(name, "unique_key", dup == 0, f"{dup} duplicate keys on ({k})")
        q(name, "date_not_null", stats[4] == 0, f"{stats[4]} null {dcol}")
        if name in ("nse_equity_daily", "bse_equity_daily_bhavcopy", "bse_equity_daily_api", "nse_indices_daily"):
            bad = con.execute(f"""SELECT count(*) FROM {src}
                WHERE close <= 0 OR high < low OR high < greatest(open, close) - 1e-6
                   OR low > least(open, close) + 1e-6""").fetchone()[0]
            share = bad / max(stats[2], 1)
            q(name, "ohlc_consistency", share < 0.001, f"{bad} rows ({share:.4%}) violate OHLC bounds",
              warn=share < 0.01)
        if name == "nse_fo_daily":
            bad = con.execute(f"""SELECT count(*) FROM {src} WHERE expiry_date IS NULL
                OR instrument_class NOT IN ('FUT','OPT')
                OR (instrument_class='OPT' AND (strike_price IS NULL OR option_type NOT IN ('CE','PE','CA','PA')))""").fetchone()[0]
            q(name, "contract_terms_complete", bad == 0, f"{bad} contracts missing expiry/strike/option type")
        if name == "nse_option_greeks":
            bad = con.execute(f"""SELECT count(*) FROM {src} WHERE iv_status = 'OK' AND (implied_vol IS NULL
                OR implied_vol <= 0 OR implied_vol > 10 OR gamma < 0 OR vega < 0
                OR (option_type = 'CE' AND (delta < 0 OR delta > 1))
                OR (option_type = 'PE' AND (delta > 0 OR delta < -1)))""").fetchone()[0]
            q(name, "greeks_in_bounds", bad == 0, f"{bad} OK rows with IV/Greeks outside bounds")
        if name == "amfi_mf_nav":
            bad = con.execute(f"SELECT count(*) FROM {src} WHERE nav IS NULL OR nav <= 0").fetchone()[0]
            q(name, "nav_positive", bad / max(stats[2], 1) < 0.001, f"{bad} rows null/non-positive NAV",
              warn=True)
    missing_sha = int(manifest.sha256.isna().sum())
    q("all", "checksums_recorded", missing_sha == 0, f"{missing_sha} files without sha256")
    return pd.DataFrame(coverage), pd.DataFrame(quality)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--no-checksums", action="store_true")
    a = p.parse_args()
    manifest = build_manifest(not a.no_checksums)
    cov, qual = audit(manifest)
    cov.to_csv(OUT / "coverage_report.csv", index=False)
    qual.to_csv(OUT / "quality_report.csv", index=False)
    summary = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "files": len(manifest), "bytes": int(manifest.bytes.sum()),
               "tests": qual.result.value_counts().to_dict(),
               "failed": qual[qual.result == "FAIL"].to_dict("records")}
    (OUT / "audit_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    pd.set_option("display.width", 250)
    print(cov.to_string(index=False))
    print()
    print(qual.to_string(index=False))
    print("\nFiles:", summary["files"], "| GB:", round(summary["bytes"] / 1e9, 2), "| tests:", summary["tests"])


if __name__ == "__main__":
    main()

"""BSE equity daily bhavcopy: Bronze download + Silver normalization.

One official end-of-day file per trading day covers every BSE cash-market
scrip, replacing ~1 request per scrip per month with ~1 request per day.

Formats
  legacy  EQ{ddmmyy}_CSV.ZIP                         2006 .. 2024-07-05
  udiff   BhavCopy_BSE_CM_0_0_0_{yyyymmdd}_F_0000.CSV 2024-07-08 ..
Pre-2006 files are not published at these URLs (documented gap).

Bronze : data/bronze/india/equity_prices/bse_bhavcopy/year=YYYY/<original name>
Silver : data/silver/india/equity_prices/bse_bhavcopy/year=YYYY/month=MM/bse_bhavcopy_YYYYMMDD.parquet
Quarantine: data/quarantine/india/equity_prices/bse_bhavcopy/year=YYYY/month=MM/  (duplicate / invalid OHLC rows + reason)
Log    : data/staging/india/bse_bhavcopy_log.csv  (one row per weekday attempt)

Conventions follow india_bse_historical_normalizer.py (per-scrip API pipeline):
  * instrument_id / exchange_listing_id come from the registry
    (exchange_listings, exchange_mic='XBOM', listing_code = scrip code);
    rows not in the registry keep NULL instrument_id and registry_status='UNLINKED'.
  * observation_id = sha256(scrip_code|trade_date|source_format)
  * Duplicate observation_ids and invalid OHLC never reach Silver; they are
    quarantined with a reason instead of being dropped.
Delivery quantity / weighted price are NOT in bhavcopy; they come from the
per-scrip API dataset (silver/.../bse/) for the scrips/months enriched there.

Restartable: existing Bronze files are re-validated, not re-downloaded;
existing Silver files are skipped unless --renormalize.
Non-ZIP/CSV responses (BSE returns an HTML page for holidays) are logged
as UNAVAILABLE and never written to Bronze.
"""
import argparse
import csv
import hashlib
import io
import os
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import sqlite3

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data/bronze/india/equity_prices/bse_bhavcopy"
SILVER = ROOT / "data/silver/india/equity_prices/bse_bhavcopy"
QUARANTINE = ROOT / "data/quarantine/india/equity_prices/bse_bhavcopy"
LOG = ROOT / "data/staging/india/bse_bhavcopy_log.csv"
MASTER = ROOT / "data/silver/reference/india/bse_security_master_v2.parquet"
DB = ROOT / "data/registry/quantera_registry.db"


def silver_path(day):
    return SILVER / f"year={day.year}" / f"month={day.month:02d}" / f"bse_bhavcopy_{day:%Y%m%d}.parquet"


def load_registry():
    with sqlite3.connect(DB) as conn:
        reg = pd.read_sql_query(
            "SELECT listing_code, instrument_id, exchange_listing_id "
            "FROM exchange_listings WHERE exchange_mic = 'XBOM'", conn)
    reg["listing_code"] = reg.listing_code.astype(str).str.strip()
    return reg.drop_duplicates("listing_code").set_index("listing_code")

BASE = "https://www.bseindia.com/download/BhavCopy/Equity/"
UDIFF_START = date(2024, 7, 8)
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bseindia.com/"}
LOG_FIELDS = ["trade_date", "format", "download_status", "http_status", "bytes",
              "sha256", "silver_status", "rows", "quarantined", "unlinked", "details", "logged_utc"]
SCHEMA_VERSION = "bse_bhavcopy_v2"


def source_name(day):
    if day >= UDIFF_START:
        return "udiff", f"BhavCopy_BSE_CM_0_0_0_{day:%Y%m%d}_F_0000.CSV"
    return "legacy", f"EQ{day:%d%m%y}_CSV.ZIP"


def is_valid_payload(fmt, content):
    if fmt == "legacy":
        return content[:2] == b"PK"
    return content[:7] == b"TradDt,"


def download(day, session, retries, pause):
    fmt, name = source_name(day)
    target = BRONZE / f"year={day.year}" / name
    if target.exists() and is_valid_payload(fmt, target.read_bytes()[:16]):
        return fmt, target, "EXISTING", "", ""
    last = ""
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(BASE + name, timeout=(10, 60))
            if resp.status_code == 404:
                return fmt, None, "UNAVAILABLE", 404, "HTTP 404"
            if resp.status_code in (429,) or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            if resp.status_code == 403:
                return fmt, None, "FAILED", 403, "HTTP 403 forbidden"
            if not is_valid_payload(fmt, resp.content[:16]):
                # BSE serves its HTML shell for non-trading days.
                return fmt, None, "UNAVAILABLE", resp.status_code, "non-data response (holiday?)"
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(resp.content)
            os.replace(tmp, target)
            return fmt, target, "DOWNLOADED", resp.status_code, f"attempt {attempt}"
        except requests.RequestException as exc:
            last = str(exc)[:300]
            time.sleep(min(60, pause * 5 * 2 ** (attempt - 1)))
    return fmt, None, "FAILED", "", last


def read_legacy(path):
    with zipfile.ZipFile(path) as z:
        member = [n for n in z.namelist() if n.upper().endswith(".CSV")][0]
        text = z.read(member).decode("latin-1")
    # Some source files drop a line break, merging two records into one line
    # (e.g. EQ291221: 13 + 14 = 27 fields). Split them back; flag the rows.
    rows = list(csv.reader(io.StringIO(text)))
    header = [c.strip() for c in rows[0]]
    n = len(header)
    fixed, repaired = [], []
    for r in rows[1:]:
        if not r:
            continue
        flag = None
        while len(r) > n and len(r) >= 2 * n - 1:
            fixed.append(r[:n - 1] + [""])
            repaired.append("SPLIT_MERGED_LINE")
            r = r[n - 1:]
            flag = "SPLIT_MERGED_LINE"
        if len(r) != n:
            raise ValueError(f"unrepairable line with {len(r)} fields: {','.join(r)[:120]}")
        fixed.append(r)
        repaired.append(flag)
    df = pd.DataFrame(fixed, columns=header)
    df["SC_CODE"] = df.SC_CODE.astype(str)
    return pd.DataFrame({
        "source_repair": repaired,
        "bse_scrip_code": df.SC_CODE.str.strip(),
        "isin": pd.NA,
        "symbol": pd.NA,
        "company_name": df.SC_NAME.astype(str).str.strip(),
        "security_series": df.SC_GROUP.astype(str).str.strip(),
        "security_type": df.SC_TYPE.astype(str).str.strip(),
        "open": df.OPEN, "high": df.HIGH, "low": df.LOW, "close": df.CLOSE,
        "last_price": df.LAST, "previous_close": df.PREVCLOSE,
        "volume": df.NO_OF_SHRS, "traded_value": df.NET_TURNOV,
        "trade_count": df.NO_TRADES,
    })


def read_udiff(path):
    df = pd.read_csv(path, dtype={"FinInstrmId": str})
    return pd.DataFrame({
        "source_repair": pd.NA,
        "bse_scrip_code": df.FinInstrmId.str.strip(),
        "isin": df.ISIN,
        "symbol": df.TckrSymb,
        "company_name": df.FinInstrmNm,
        "security_series": df.SctySrs,
        "security_type": df.FinInstrmTp,
        "open": df.OpnPric, "high": df.HghPric, "low": df.LwPric, "close": df.ClsPric,
        "last_price": df.LastPric, "previous_close": df.PrvsClsgPric,
        "volume": df.TtlTradgVol, "traded_value": df.TtlTrfVal,
        "trade_count": df.TtlNbOfTxsExctd,
    })


COLUMN_ORDER = [  # shared columns first, in the API dataset's order
    "bse_scrip_code", "company_name", "bse_symbol", "isin", "instrument_id",
    "exchange_listing_id", "registry_status", "security_series", "security_type",
    "trade_date", "observation_id", "source_row_number",
    "open", "high", "low", "close", "last_price", "previous_close",
    "volume", "trade_count", "turnover",
    "exchange", "exchange_mic", "currency", "source_format", "source_file", "source_repair", "schema_version",
]


def write_atomic(df, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    df.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, out)


def normalize(day, fmt, path, master, registry):
    df = read_legacy(path) if fmt == "legacy" else read_udiff(path)
    df = df.rename(columns={"symbol": "bse_symbol", "traded_value": "turnover"})
    df = df[df.bse_scrip_code.notna() & (df.bse_scrip_code != "")].reset_index(drop=True)
    df.insert(0, "source_row_number", range(1, len(df) + 1))

    # ISIN/symbol from the security master where the file lacks them (legacy format).
    m = master.set_index("bse_scrip_code")
    df["isin"] = df["isin"].fillna(df.bse_scrip_code.map(m["isin"]))
    df["bse_symbol"] = df["bse_symbol"].fillna(df.bse_scrip_code.map(m["bse_symbol"]))

    # Registry linkage, same rule as the per-scrip API normalizer.
    df["instrument_id"] = df.bse_scrip_code.map(registry["instrument_id"])
    df["exchange_listing_id"] = df.bse_scrip_code.map(registry["exchange_listing_id"])
    df["registry_status"] = df.instrument_id.notna().map({True: "LINKED", False: "UNLINKED"})

    for c in ["open", "high", "low", "close", "last_price", "previous_close", "turnover"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    for c in ["volume", "trade_count"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")

    df["trade_date"] = pd.Timestamp(day)
    df["observation_id"] = [
        hashlib.sha256(f"{code}|{day.isoformat()}|{fmt}".encode()).hexdigest()
        for code in df.bse_scrip_code]
    df["exchange"] = "BSE"
    df["exchange_mic"] = "XBOM"
    df["currency"] = "INR"
    df["source_format"] = fmt
    df["source_file"] = str(path.relative_to(ROOT))
    df["schema_version"] = SCHEMA_VERSION

    # Quarantine rules: duplicate observation_id, invalid OHLC (API normalizer rule) or non-positive close.
    reason = pd.Series(pd.NA, index=df.index, dtype="string")
    dup = df.observation_id.duplicated(keep=False)
    reason[dup] = "DUPLICATE_OBSERVATION"
    invalid = ((df.high < df.low) | (df.high < df.open) | (df.high < df.close)
               | (df.low > df.open) | (df.low > df.close) | (df.close <= 0)).fillna(False)
    reason[invalid & reason.isna()] = "INVALID_OHLC"

    df = df[COLUMN_ORDER]
    for c in ["bse_scrip_code", "company_name", "bse_symbol", "isin", "instrument_id",
              "exchange_listing_id", "registry_status", "security_series", "security_type", "source_repair"]:
        df[c] = df[c].astype("string")

    bad = reason.notna()
    if bad.any():
        q = df[bad].assign(quarantine_reason=reason[bad])
        write_atomic(q, QUARANTINE / f"year={day.year}" / f"month={day.month:02d}"
                     / f"bse_bhavcopy_{day:%Y%m%d}_quarantine.parquet")
    good = df[~bad]
    write_atomic(good, silver_path(day))
    return len(good), int(bad.sum()), int((good.registry_status == "UNLINKED").sum())


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.add_argument("--pause", type=float, default=0.5)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--renormalize", action="store_true")
    a = p.parse_args()

    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    master = pd.read_parquet(MASTER, columns=["bse_scrip_code", "isin", "bse_symbol"])
    master["bse_scrip_code"] = master.bse_scrip_code.astype(str)
    master = master.drop_duplicates("bse_scrip_code")
    registry = load_registry()

    session = requests.Session()
    session.headers.update(HEADERS)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    new_log = not LOG.exists()
    counts = {}

    with LOG.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if new_log:
            w.writeheader()
        day = start
        while day <= end:
            if day.weekday() >= 5:
                day += timedelta(days=1)
                continue
            out = silver_path(day)
            if out.exists() and not a.renormalize:
                counts["SKIPPED"] = counts.get("SKIPPED", 0) + 1
                day += timedelta(days=1)
                continue

            fmt, path, status, http, details = download(day, session, a.retries, a.pause)
            row = {"trade_date": day.isoformat(), "format": fmt, "download_status": status,
                   "http_status": http, "details": details,
                   "logged_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            if path is not None:
                content = path.read_bytes()
                row["bytes"] = len(content)
                row["sha256"] = hashlib.sha256(content).hexdigest()
                try:
                    n, quarantined, unlinked = normalize(day, fmt, path, master, registry)
                    row.update(silver_status="NORMALIZED", rows=n,
                               quarantined=quarantined, unlinked=unlinked)
                except Exception as exc:  # noqa: BLE001
                    row.update(silver_status="FAILED", details=f"normalize: {exc}"[:300])
            else:
                row["silver_status"] = "NOT_RUN"
            w.writerow(row)
            fh.flush()
            key = f"{status}/{row['silver_status']}"
            counts[key] = counts.get(key, 0) + 1
            print(day, fmt, status, row["silver_status"], row.get("rows", ""), flush=True)
            if status != "EXISTING":
                time.sleep(a.pause)
            day += timedelta(days=1)

    print("\nBSE BHAVCOPY SUMMARY")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()

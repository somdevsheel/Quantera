"""NSE futures & options daily bhavcopy: Bronze + Silver.

Formats
  legacy  content/historical/DERIVATIVES/YYYY/MON/foDDMONYYYYbhav.csv.zip   2000-06 .. 2024-07-05
  udiff   content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip          2024-07-08 ..
Bronze : data/bronze/india/derivatives/nse_fo/year=YYYY/<original name>
Silver : data/silver/india/derivatives/nse_fo/year=YYYY/nse_fo_YYYYMMDD.parquet
Log    : data/staging/india/nse_fo_log.csv

Every contract keeps expiry, strike, option type and a deterministic
contract_id. Units follow the source and are NOT silently converted:
  legacy: contracts (count), traded_value_inr = VAL_INLAKH * 1e5
  udiff : traded_quantity (TtlTradgVol), traded_value_inr (TtlTrfVal),
          lot_size (NewBrdLotQty), underlying_price
Implied volatility and Greeks are not published in bhavcopy (documented gap).
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

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data/bronze/india/derivatives/nse_fo"
SILVER = ROOT / "data/silver/india/derivatives/nse_fo"
LOG = ROOT / "data/staging/india/nse_fo_log.csv"
UDIFF_START = date(2024, 7, 8)
SCHEMA_VERSION = "nse_fo_v2"
LOG_FIELDS = ["trade_date", "format", "download_status", "http_status", "bytes", "sha256",
              "silver_status", "rows", "details", "logged_utc"]

LEGACY_TYPES = {  # instrument -> (instrument_class, underlying_type)
    "FUTIDX": ("FUT", "INDEX"), "FUTSTK": ("FUT", "STOCK"), "FUTIVX": ("FUT", "VOLATILITY"),
    "FUTINT": ("FUT", "INTEREST_RATE"), "UNDINT": ("FUT", "INTEREST_RATE"),
    "OPTIDX": ("OPT", "INDEX"), "OPTSTK": ("OPT", "STOCK"),
}
QUARANTINE = ROOT / "data/quarantine/india/derivatives/nse_fo"
UDIFF_TYPES = {"IDF": ("FUT", "INDEX"), "STF": ("FUT", "STOCK"),
               "IDO": ("OPT", "INDEX"), "STO": ("OPT", "STOCK")}


def source(day):
    if day >= UDIFF_START:
        name = f"BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
        return "udiff", name, f"https://nsearchives.nseindia.com/content/fo/{name}"
    mon = day.strftime("%b").upper()
    name = f"fo{day:%d}{mon}{day:%Y}bhav.csv.zip"
    return "legacy", name, f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{day:%Y}/{mon}/{name}"


def download(day, session, retries):
    fmt, name, url = source(day)
    target = BRONZE / f"year={day.year}" / name
    if target.exists() and target.read_bytes()[:2] == b"PK":
        return fmt, target, "EXISTING", "", ""
    last = ""
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=(10, 90))
            if r.status_code == 404:
                return fmt, None, "UNAVAILABLE", 404, "HTTP 404"
            r.raise_for_status()
            if r.content[:2] != b"PK":
                return fmt, None, "UNAVAILABLE", r.status_code, "non-zip response"
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".zip.tmp")
            tmp.write_bytes(r.content)
            os.replace(tmp, target)
            return fmt, target, "DOWNLOADED", r.status_code, f"attempt {attempt}"
        except requests.RequestException as exc:
            last = str(exc)[:300]
            time.sleep(3 * 2 ** (attempt - 1))
    return fmt, None, "FAILED", "", last


def read_zip(path):
    """Read the bhavcopy CSV. Some legacy files drop a line break so two records
    share one line (e.g. 16 + 15 = 31 fields, the trailing empty field merging
    with the next record). Such lines are split back and flagged in
    `source_repair`; Bronze is never modified."""
    with zipfile.ZipFile(path) as z:
        member = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
        text = z.read(member).decode("latin-1")
    try:
        df = pd.read_csv(io.StringIO(text), low_memory=False)
        df["source_repair"] = pd.NA
    except pd.errors.ParserError:
        rows = list(csv.reader(io.StringIO(text)))
        header = [c.strip() for c in rows[0]]
        n = len(header)
        fixed, flags = [], []
        for r in rows[1:]:
            if not r:
                continue
            flag = None
            while len(r) >= 2 * n - 1:
                fixed.append(r[:n - 1] + [""])
                flags.append("SPLIT_MERGED_LINE")
                r = r[n - 1:]
                flag = "SPLIT_MERGED_LINE"
            if len(r) != n:
                raise ValueError(f"unrepairable line with {len(r)} fields")
            fixed.append(r)
            flags.append(flag)
        df = pd.DataFrame(fixed, columns=header)
        df["source_repair"] = flags
    df.columns = [str(c).strip() for c in df.columns]
    return df.loc[:, ~df.columns.str.startswith("Unnamed")]


def num(s):
    return pd.to_numeric(s, errors="coerce").astype("float64")


def normalize(day, fmt, path):
    raw = read_zip(path)
    if fmt == "legacy":
        raw = raw.rename(columns={"OPTIONTYPE": "OPTION_TYP"})
        # Source artifacts: repeated header lines ("INSTRUMENT") and footer notes ("*Updated ...").
        junk = ~raw.INSTRUMENT.astype(str).str.strip().isin(LEGACY_TYPES)
        if junk.any():
            q = raw[junk].astype(str).assign(quarantine_reason="NON_CONTRACT_ROW", trade_date=str(day))
            qp = QUARANTINE / f"year={day.year}" / f"nse_fo_{day:%Y%m%d}_quarantine.parquet"
            qp.parent.mkdir(parents=True, exist_ok=True)
            q.to_parquet(qp, index=False)
            raw = raw[~junk]
        cls = raw.INSTRUMENT.str.strip().map(lambda x: LEGACY_TYPES.get(x, ("UNKNOWN", "UNKNOWN")))
        df = pd.DataFrame({
            "source_instrument_type": raw.INSTRUMENT.str.strip(),
            "symbol": raw.SYMBOL.str.strip(),
            "expiry_date": pd.to_datetime(raw.EXPIRY_DT, format="mixed", dayfirst=True, errors="coerce"),
            "strike_price": num(raw.STRIKE_PR),
            "option_type": raw.OPTION_TYP.astype(str).str.strip(),
            "open": num(raw.OPEN), "high": num(raw.HIGH), "low": num(raw.LOW),
            "close": num(raw.CLOSE), "settlement_price": num(raw.SETTLE_PR),
            # float: NSE published fractional contract counts in 2001 (e.g. 0.5); kept as reported.
            "contracts": num(raw.CONTRACTS),
            "traded_quantity": pd.array([pd.NA] * len(raw), dtype="Int64"),
            "traded_value_inr": num(raw.VAL_INLAKH) * 1e5,
            "open_interest": pd.to_numeric(raw.OPEN_INT, errors="coerce").astype("Int64"),
            "change_in_oi": pd.to_numeric(raw.CHG_IN_OI, errors="coerce").astype("Int64"),
            "lot_size": pd.array([pd.NA] * len(raw), dtype="Int64"),
            "underlying_price": float("nan"),
            "exchange_contract_id": pd.NA,
            "source_repair": raw.source_repair.values,
        })
    else:
        cls = raw.FinInstrmTp.str.strip().map(lambda x: UDIFF_TYPES.get(x, ("UNKNOWN", "UNKNOWN")))
        df = pd.DataFrame({
            "source_instrument_type": raw.FinInstrmTp.str.strip(),
            "symbol": raw.TckrSymb.str.strip(),
            "expiry_date": pd.to_datetime(raw.XpryDt, errors="coerce"),
            "strike_price": num(raw.StrkPric),
            "option_type": raw.OptnTp.astype("string").str.strip(),
            "open": num(raw.OpnPric), "high": num(raw.HghPric), "low": num(raw.LwPric),
            "close": num(raw.ClsPric), "settlement_price": num(raw.SttlmPric),
            "contracts": float("nan"),
            "traded_quantity": pd.to_numeric(raw.TtlTradgVol, errors="coerce").astype("Int64"),
            "traded_value_inr": num(raw.TtlTrfVal),
            "open_interest": pd.to_numeric(raw.OpnIntrst, errors="coerce").astype("Int64"),
            "change_in_oi": pd.to_numeric(raw.ChngInOpnIntrst, errors="coerce").astype("Int64"),
            "lot_size": pd.to_numeric(raw.NewBrdLotQty, errors="coerce").astype("Int64"),
            "underlying_price": num(raw.UndrlygPric),
            "exchange_contract_id": raw.FinInstrmId.astype("string"),
            "source_repair": raw.source_repair.values,
        })
    df.insert(0, "trade_date", pd.Timestamp(day))
    df["instrument_class"] = [c[0] for c in cls]
    df["underlying_type"] = [c[1] for c in cls]
    # Futures carry "XX"/blank option type in source; normalize to FUT.
    df.loc[df.instrument_class == "FUT", "option_type"] = "FUT"
    df.loc[df.instrument_class == "FUT", "strike_price"] = float("nan")
    strike = df.strike_price.map(lambda v: "" if pd.isna(v) else f"{v:g}")
    df["contract_id"] = ("NSEFO:" + df.instrument_class + ":" + df.symbol + ":"
                         + df.expiry_date.dt.strftime("%Y-%m-%d").fillna("NA") + ":"
                         + strike + ":" + df.option_type.fillna("NA").astype(str))
    df = df.drop_duplicates("contract_id", keep="last")
    df["exchange"] = "NSE"
    df["currency"] = "INR"
    df["source_format"] = fmt
    df["source_file"] = str(path.relative_to(ROOT))
    df["schema_version"] = SCHEMA_VERSION
    for c in ["source_instrument_type", "symbol", "option_type", "instrument_class",
              "underlying_type", "contract_id", "exchange_contract_id", "source_repair"]:
        df[c] = df[c].astype("string")
    out = SILVER / f"year={day.year}" / f"nse_fo_{day:%Y%m%d}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, out)
    return len(df)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--pause", type=float, default=0.6)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--renormalize", action="store_true")
    a = p.parse_args()
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com/all-reports"})
    try:
        s.get("https://www.nseindia.com/all-reports", timeout=30)
    except requests.RequestException:
        pass
    LOG.parent.mkdir(parents=True, exist_ok=True)
    new = not LOG.exists()
    counts = {}
    with LOG.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        day = start
        while day <= end:
            out = SILVER / f"year={day.year}" / f"nse_fo_{day:%Y%m%d}.parquet"
            if day.weekday() >= 5 or (out.exists() and not a.renormalize):
                day += timedelta(days=1)
                continue
            fmt, path, status, http, details = download(day, s, a.retries)
            row = {"trade_date": day.isoformat(), "format": fmt, "download_status": status,
                   "http_status": http, "details": details,
                   "logged_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            if path:
                b = path.read_bytes()
                row.update(bytes=len(b), sha256=hashlib.sha256(b).hexdigest())
                try:
                    row.update(silver_status="NORMALIZED", rows=normalize(day, fmt, path))
                except Exception as exc:  # noqa: BLE001
                    row.update(silver_status="FAILED", details=f"normalize: {exc}"[:300])
            else:
                row["silver_status"] = "NOT_RUN"
            w.writerow(row)
            fh.flush()
            k = f"{status}/{row['silver_status']}"
            counts[k] = counts.get(k, 0) + 1
            print(day, fmt, status, row["silver_status"], row.get("rows", ""), flush=True)
            if status != "EXISTING":
                time.sleep(a.pause)
            day += timedelta(days=1)
    print("\nNSE F&O SUMMARY")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()

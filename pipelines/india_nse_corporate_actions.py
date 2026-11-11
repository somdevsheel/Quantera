"""NSE equity corporate actions (dividends, splits, bonuses, rights, buybacks,
schemes of arrangement, ...): Bronze + Silver + Gold adjustment factors.

Source : https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date=&to_date=
         (cookie-gated JSON; one request per calendar month; history from ~2000)
Bronze : data/bronze/india/corporate_actions/nse/year=YYYY/nse_corpact_YYYYMM.json
Silver : data/silver/india/corporate_actions/nse_corporate_actions.parquet
Gold   : data/gold/india/corporate_actions/nse_price_adjustment_factors.parquet
Log    : data/staging/india/nse_corporate_actions_log.csv

Adjustment methodology (see docs/india_data_lake.md):
  Silver prices are raw exchange prints (unadjusted). Gold factors are
  multiplicative factors to apply to prices strictly BEFORE ex_date:
    SPLIT  face value F_old -> F_new : price_factor = F_new / F_old
    BONUS  a:b (a new for every b held): price_factor = b / (a + b)
  volume_factor = 1 / price_factor. Dividends are NOT price-adjusted here;
  they are kept in Silver with parsed cash amount for total-return builds.
  Rights, buybacks, demergers and schemes are recorded but not auto-adjusted.
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
BRONZE = ROOT / "data/bronze/india/corporate_actions/nse"
SILVER = ROOT / "data/silver/india/corporate_actions/nse_corporate_actions.parquet"
GOLD = ROOT / "data/gold/india/corporate_actions/nse_price_adjustment_factors.parquet"
LOG = ROOT / "data/staging/india/nse_corporate_actions_log.csv"
API = "https://www.nseindia.com/api/corporates-corporateActions"
PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-actions"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
           "Accept": "application/json", "Referer": PAGE}
SCHEMA_VERSION = "nse_corpact_v1"

RULES = [  # order matters: first match is primary type; all matches are kept
    ("SPLIT", r"\b(fv|face\s*value)\b.*\bsplit|sub[\s-]?division|\bsplit\b"),
    ("BONUS", r"\bbonus\b"),
    ("RIGHTS", r"\brights?\b"),
    ("BUYBACK", r"buy[\s-]?back"),
    ("DEMERGER", r"de-?merger|spin[\s-]?off"),
    ("MERGER_SCHEME", r"amalgamation|merger|scheme of arrangement|arrangement"),
    ("CAPITAL_REDUCTION", r"capital reduction|reduction of capital"),
    ("DIVIDEND", r"\bdiv(idend)?\b|\bdiv[-\s.]"),
    ("DISTRIBUTION", r"distribution|interest|redemption|income"),
    ("MEETING", r"\bagm\b|\begm\b|annual general|extra.?ordinary"),
]
NUM = r"(\d+(?:\.\d+)?)"


def classify(subject):
    s = subject.lower()
    hits = [name for name, rx in RULES if re.search(rx, s)]
    return (hits[0] if hits else "OTHER"), "|".join(hits)


def dividend_amount(subject):
    s = subject.replace(",", "")
    m = re.search(r"(?:rs\.?|re\.?|inr|₹)\s*-?\s*" + NUM, s, re.I)
    return float(m.group(1)) if m else None


def dividend_pct(subject):
    m = re.search(NUM + r"\s*%", subject)
    return float(m.group(1)) if m else None


SPLIT_RX = re.compile(
    r"split\D*?(?:rs|re)?\.?\s*" + NUM + r"\s*/?-?\s*(?:per\s+share\s*)?to\s*"
    r"(?:face\s+value\s*)?(?:rs|re)?\.?\s*" + NUM, re.I)


def split_ratio(subject):
    m = SPLIT_RX.search(subject)
    if m and float(m.group(1)) > 0 and float(m.group(2)) > 0:
        return float(m.group(1)), float(m.group(2))
    return None, None


def bonus_ratio(subject):
    m = re.search(r"bonus\D*" + r"(\d+)\s*:\s*(\d+)", subject, re.I)
    if m and int(m.group(1)) > 0 and int(m.group(2)) > 0:
        return int(m.group(1)), int(m.group(2))
    return None, None


def to_date(v):
    return pd.to_datetime(v, format="%d-%b-%Y", errors="coerce") if v not in (None, "-", "") else pd.NaT


def fetch(start, end, pause):
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get(PAGE, timeout=30)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    new = not LOG.exists()
    today = date.today()
    with LOG.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["period", "status", "records", "sha256", "details", "logged_utc"])
        if new:
            w.writeheader()
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            target = BRONZE / f"year={y}" / f"nse_corpact_{y:04d}{m:02d}.json"
            current = (y, m) >= (today.year, today.month - 1 if today.month > 1 else 12)
            if not target.exists() or current:
                row = {"period": f"{y:04d}{m:02d}", "logged_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                last = min(date(y, m, calendar.monthrange(y, m)[1]), today)
                params = {"index": "equities", "from_date": f"01-{m:02d}-{y}", "to_date": last.strftime("%d-%m-%Y")}
                for attempt in range(1, 4):
                    try:
                        r = s.get(API, params=params, timeout=60)
                        if r.status_code in (401, 403):
                            s.get(PAGE, timeout=30)  # refresh cookies
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
                time.sleep(pause)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def build():
    records = []
    for f in sorted(BRONZE.glob("year=*/nse_corpact_*.json")):
        for rec in json.loads(f.read_text()):
            rec["_source_file"] = str(f.relative_to(ROOT))
            records.append(rec)
    raw = pd.DataFrame(records)
    df = pd.DataFrame({
        "symbol": raw.symbol.astype("string"),
        "isin": raw["isin"].astype("string"),
        "company_name": raw.comp.astype("string"),
        "series": raw.series.astype("string"),
        "face_value": pd.to_numeric(raw.faceVal, errors="coerce"),
        "subject": raw.subject.astype("string").str.strip(),
        "ex_date": raw.exDate.map(to_date),
        "record_date": raw.recDate.map(to_date),
        "book_closure_start": raw.bcStartDate.map(to_date),
        "book_closure_end": raw.bcEndDate.map(to_date),
        "no_delivery_start": raw.ndStartDate.map(to_date),
        "no_delivery_end": raw.ndEndDate.map(to_date),
        "source_file": raw._source_file,
    })
    cls = df.subject.fillna("").map(classify)
    df["action_type"] = [c[0] for c in cls]
    df["action_flags"] = [c[1] for c in cls]
    df["dividend_per_share_inr"] = df.subject.fillna("").map(
        lambda s: dividend_amount(s) if re.search(r"div", s, re.I) else None)
    df["dividend_pct_of_fv"] = df.subject.fillna("").map(
        lambda s: dividend_pct(s) if re.search(r"div", s, re.I) else None)
    sr = df.subject.fillna("").map(split_ratio)
    df["split_fv_old"] = [x[0] for x in sr]
    df["split_fv_new"] = [x[1] for x in sr]
    br = df.subject.fillna("").map(bonus_ratio)
    df["bonus_new"] = pd.array([x[0] for x in br], dtype="Int64")
    df["bonus_held"] = pd.array([x[1] for x in br], dtype="Int64")
    df["instrument_id"] = df["isin"].map(lambda x: f"IN:{x}" if isinstance(x, str) and x.startswith("IN") else pd.NA).astype("string")
    df["exchange"] = "NSE"
    df["source"] = "NSE corporate actions API"
    df["schema_version"] = SCHEMA_VERSION
    df = df.drop_duplicates(subset=["symbol", "isin", "ex_date", "subject"])
    for c in ["action_type", "action_flags", "exchange", "source", "schema_version", "source_file"]:
        df[c] = df[c].astype("string")
    SILVER.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(SILVER, index=False, compression="zstd")

    # Gold: price adjustment factors for splits and bonuses.
    split = df[(df.action_flags.str.contains("SPLIT")) & df.split_fv_old.notna() & df.ex_date.notna()].copy()
    split["event"] = "SPLIT"
    split["price_factor"] = split.split_fv_new / split.split_fv_old
    bonus = df[(df.action_flags.str.contains("BONUS")) & df.bonus_new.notna() & df.ex_date.notna()].copy()
    bonus["event"] = "BONUS"
    bonus["price_factor"] = bonus.bonus_held.astype(float) / (bonus.bonus_new + bonus.bonus_held).astype(float)
    adj = pd.concat([split, bonus], ignore_index=True)
    adj = adj[(adj.price_factor > 0) & (adj.price_factor < 1)]  # sanity: both events reduce price
    adj["volume_factor"] = 1 / adj.price_factor
    adj = adj[["instrument_id", "isin", "symbol", "ex_date", "event", "price_factor", "volume_factor",
               "subject", "split_fv_old", "split_fv_new", "bonus_new", "bonus_held", "source_file"]]
    adj = adj.sort_values(["isin", "ex_date"]).drop_duplicates(["isin", "ex_date", "event"])
    # Same-day split + bonus must compound: apply combined_price_factor once per (isin, ex_date).
    adj["combined_price_factor"] = adj.groupby(["isin", "ex_date"]).price_factor.transform("prod")
    adj["events_on_ex_date"] = adj.groupby(["isin", "ex_date"]).event.transform(lambda s: "+".join(sorted(s)))
    GOLD.parent.mkdir(parents=True, exist_ok=True)
    adj.to_parquet(GOLD, index=False, compression="zstd")

    unparsed = {
        "SPLIT": int(((df.action_flags.str.contains("SPLIT")) & df.split_fv_old.isna()).sum()),
        "BONUS": int(((df.action_flags.str.contains("BONUS")) & df.bonus_new.isna()).sum()),
        "DIVIDEND": int(((df.action_type == "DIVIDEND") & df.dividend_per_share_inr.isna() & df.dividend_pct_of_fv.isna()).sum()),
    }
    print("Silver rows:", len(df), "| ex_date range:", df.ex_date.min(), "->", df.ex_date.max())
    print(df.action_type.value_counts().to_string())
    print("Gold adjustment factors:", len(adj), adj.event.value_counts().to_dict())
    print("Unparsed (kept, not adjusted):", unparsed)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", default="2000-01")
    p.add_argument("--end", default=date.today().strftime("%Y-%m"))
    p.add_argument("--pause", type=float, default=1.5)
    p.add_argument("--build-only", action="store_true")
    a = p.parse_args()
    if not a.build_only:
        fetch(date.fromisoformat(a.start + "-01"), date.fromisoformat(a.end + "-01"), a.pause)
    build()


if __name__ == "__main__":
    main()

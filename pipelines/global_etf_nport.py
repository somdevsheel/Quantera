"""US ETF holdings + net assets from SEC Form N-PORT (public quarterly filings).

Universe: registered ETFs (ingestion_registry asset_class='ETF') mapped to SEC fund
series via https://www.sec.gov/files/company_tickers_mf.json. ETFs that do not file
N-PORT (unit investment trusts such as SPY; grantor/commodity trusts such as GLD, IAU,
SLV) are recorded as NOT_NPORT_FILER, not silently dropped.

Bronze : data/bronze/funds/sec_nport/series=<S...>/<accession>.xml        (immutable)
Silver : data/silver/funds/etf_holdings/series=<S...>/<accession>.parquet  (one row per holding)
         data/silver/funds/etf_nport_filings.parquet                       (one row per filing)
Gold   : data/gold/funds/etf_holdings.parquet  latest amendment per (series, report_date)
Ledger : data/validation/funds/etf_nport_manifest.csv  (download/normalize status, sha256)
         data/validation/funds/etf_nport_universe.csv  (ticker -> series, filer status)
Registry: fills etf_metadata (issuer, fund_name, aum, domicile) for linked ETFs. aum is set
only for single-share-class series; multi-class series (Vanguard) report fund-level net assets
covering all classes, kept in etf_nport_filings (net_assets_usd, share_class_count), aum NULL.
QQQ files N-PORT only since its 2025 conversion from a UIT.

Checks (fail-closed -> status, not silent): holdings present; sum(pct_of_nav) within
[50, 150] (derivatives, cash and short positions make it differ from 100); net assets > 0.
Public N-PORT is the fiscal-quarter-end report (filed ~60 days after period end).

Run: python pipelines/global_etf_nport.py [--stage download|normalize|all] [--pause 0.2]
"""
import argparse
import hashlib
import json
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data/registry/quantera_registry.db"
BRONZE = ROOT / "data/bronze/funds/sec_nport"
SILVER = ROOT / "data/silver/funds/etf_holdings"
FILINGS = ROOT / "data/silver/funds/etf_nport_filings.parquet"
GOLD = ROOT / "data/gold/funds/etf_holdings.parquet"
VAL = ROOT / "data/validation/funds"
MANIFEST = VAL / "etf_nport_manifest.csv"
UNIVERSE = VAL / "etf_nport_universe.csv"
HEADERS = {"User-Agent": "FinancialDataLake/1.0 somdevsheel7@gmail.com"}
NS = {"n": "http://www.sec.gov/edgar/nport"}
SCHEMA = "etf_nport_v1"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get(url, pause):
    for attempt in range(4):
        r = requests.get(url, headers=HEADERS, timeout=60)
        time.sleep(pause)
        if r.status_code == 200:
            return r
        if r.status_code in (429, 503):
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
    r.raise_for_status()


def universe(pause):
    con = sqlite3.connect(REGISTRY)
    etfs = pd.read_sql("SELECT instrument_id, symbol, exchange_mic FROM ingestion_registry "
                       "WHERE asset_class='ETF' AND status='SUCCESS'", con)
    mf = get("https://www.sec.gov/files/company_tickers_mf.json", pause).json()
    m = pd.DataFrame(mf["data"], columns=mf["fields"]).drop_duplicates("symbol")
    u = etfs.merge(m, on="symbol", how="left")
    u["filer_status"] = u.seriesId.notna().map({True: "NPORT_SERIES", False: "NOT_NPORT_FILER"})
    VAL.mkdir(parents=True, exist_ok=True)
    u.to_csv(UNIVERSE, index=False)
    return u


def list_filings(series_id, pause):
    out, start = [], 0
    while True:
        url = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK="
               f"{series_id}&type=NPORT-P&dateb=&owner=include&count=100&start={start}&output=atom")
        xml = get(url, pause).text
        entries = re.findall(r"<entry>(.*?)</entry>", xml, re.S)
        for e in entries:
            acc = re.search(r"<accession-number>([^<]+)", e).group(1)
            form = re.search(r"<filing-type>([^<]+)", e).group(1)
            date = re.search(r"<filing-date>([^<]+)", e).group(1)
            href = re.search(r"<filing-href>([^<]+)", e).group(1)
            out.append({"accession": acc, "form": form, "filing_date": date, "index_url": href})
        if len(entries) < 100:
            return out
        start += 100


def download(u, pause):
    man = pd.read_csv(MANIFEST, dtype=str) if MANIFEST.exists() else pd.DataFrame()
    have = set(man[man.download_status == "DOWNLOADED"].accession) if not man.empty else set()
    rows = []
    for t in u[u.filer_status == "NPORT_SERIES"].itertuples():
        for f in list_filings(t.seriesId, pause):
            if f["accession"] in have:
                continue
            target = BRONZE / f"series={t.seriesId}" / f"{f['accession']}.xml"
            rec = {**f, "symbol": t.symbol, "series_id": t.seriesId, "class_id": t.classId,
                   "cik": str(int(t.cik)), "instrument_id": t.instrument_id,
                   "bronze_path": str(target.relative_to(ROOT)), "retrieved_utc": now()}
            try:
                if not target.exists():
                    url = f["index_url"].rsplit("/", 1)[0] + "/primary_doc.xml"
                    body = get(url, pause).content
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tmp = target.with_suffix(".xml.tmp")
                    tmp.write_bytes(body)
                    tmp.replace(target)
                data = target.read_bytes()
                rec.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), download_status="DOWNLOADED")
            except Exception as e:  # recorded, retried next run
                rec.update(download_status="FAILED", error=str(e)[:200])
            rows.append(rec)
            have.add(f["accession"])
        print(f"{t.symbol}: {sum(r['symbol'] == t.symbol for r in rows)} new filings", flush=True)
    if rows:
        man = pd.concat([man, pd.DataFrame(rows).astype(str)], ignore_index=True)
        man = man.drop_duplicates("accession", keep="last")
        man.to_csv(MANIFEST, index=False)
    return man


def txt(el, path):
    x = el.find(path, NS)
    return x.text.strip() if x is not None and x.text else None


def num(el, path):
    v = txt(el, path)
    try:
        return float(v) if v not in (None, "N/A") else None
    except ValueError:
        return None


def parse(path, meta):
    root = ET.parse(path).getroot()
    gen = root.find(".//n:formData/n:genInfo", NS)
    fund = root.find(".//n:formData/n:fundInfo", NS)
    head = {
        "accession": meta.accession, "symbol": meta.symbol, "instrument_id": meta.instrument_id,
        "series_id": txt(gen, "n:seriesId"), "series_name": txt(gen, "n:seriesName"),
        "registrant": txt(gen, "n:regName"), "cik": meta.cik, "form": meta.form,
        "filing_date": meta.filing_date, "report_date": txt(gen, "n:repPdDate"),
        "fiscal_period_end": txt(gen, "n:repPdEnd"),
        "total_assets_usd": num(fund, "n:totAssets"), "total_liabilities_usd": num(fund, "n:totLiabs"),
        "net_assets_usd": num(fund, "n:netAssets"),
        # net assets are reported for the whole series (all share classes, e.g. Vanguard
        # ETF + Admiral + Investor); only a single-class series gives the ETF's own AUM.
        "share_class_count": len(root.findall(".//n:seriesClassInfo/n:classId", NS)),
    }
    rows = []
    for i, h in enumerate(root.iterfind(".//n:invstOrSecs/n:invstOrSec", NS)):
        isin = h.find("n:identifiers/n:isin", NS)
        ticker = h.find("n:identifiers/n:ticker", NS)
        other = h.find("n:identifiers/n:other", NS)
        rows.append({
            "holding_row": i, "name": txt(h, "n:name"), "title": txt(h, "n:title"), "lei": txt(h, "n:lei"),
            "cusip": txt(h, "n:cusip"), "isin": isin.get("value") if isin is not None else None,
            "ticker": ticker.get("value") if ticker is not None else None,
            "other_id": other.get("value") if other is not None else None,
            "other_id_type": other.get("otherDesc") if other is not None else None,
            "balance": num(h, "n:balance"), "balance_units": txt(h, "n:units"),
            "currency": txt(h, "n:curCd"), "value_usd": num(h, "n:valUSD"),
            "pct_of_nav": num(h, "n:pctVal"), "payoff_profile": txt(h, "n:payoffProfile"),
            "asset_category": txt(h, "n:assetCat") or txt(h, "n:assetConditional"),
            "issuer_category": txt(h, "n:issuerCat") or txt(h, "n:issuerConditional"),
            "country": txt(h, "n:invCountry"), "fair_value_level": txt(h, "n:fairValLevel"),
            "is_restricted": txt(h, "n:isRestrictedSec"),
            "maturity_date": txt(h, "n:debtSec/n:maturityDt"), "coupon_rate": num(h, "n:debtSec/n:annualizedRt"),
        })
    df = pd.DataFrame(rows)
    pct = float(df.pct_of_nav.sum()) if not df.empty else 0.0
    status = ("NO_HOLDINGS" if df.empty else "NAV_NOT_POSITIVE" if not (head["net_assets_usd"] or 0) > 0
              else "PCT_SUM_OUT_OF_RANGE" if not 50 <= pct <= 150 else "OK")
    head.update(holdings=len(df), pct_of_nav_sum=pct, check_status=status)
    if not df.empty:
        for k in ("accession", "symbol", "instrument_id", "series_id", "report_date", "filing_date", "form"):
            df[k] = head[k]
        df["observation_id"] = [hashlib.sha256(f"{head['accession']}|{r}".encode()).hexdigest()
                                for r in df.holding_row]
        df["schema_version"] = SCHEMA
    return head, df


def normalize():
    man = pd.read_csv(MANIFEST, dtype=str)
    heads = []
    for m in man[man.download_status == "DOWNLOADED"].itertuples():
        head, df = parse(ROOT / m.bronze_path, m)
        heads.append(head)
        out = SILVER / f"series={m.series_id}" / f"{m.accession}.parquet"
        if not df.empty and not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".parquet.tmp")
            df.to_parquet(tmp, index=False, compression="zstd")
            tmp.replace(out)
    f = pd.DataFrame(heads)
    for c in ("report_date", "filing_date", "fiscal_period_end"):
        f[c] = pd.to_datetime(f[c], errors="coerce")
    # latest amendment per (series, report_date) is the current version; earlier ones kept
    f = f.sort_values(["series_id", "report_date", "filing_date", "accession"])
    f["is_latest_version"] = ~f.duplicated(["series_id", "report_date"], keep="last")
    f["schema_version"] = SCHEMA
    FILINGS.parent.mkdir(parents=True, exist_ok=True)
    f.to_parquet(FILINGS, index=False)
    latest = f[f.is_latest_version & (f.check_status == "OK")]
    parts = [pd.read_parquet(SILVER / f"series={r.series_id}" / f"{r.accession}.parquet") for r in latest.itertuples()]
    gold = pd.concat(parts, ignore_index=True)
    gold["report_date"] = pd.to_datetime(gold.report_date)
    GOLD.parent.mkdir(parents=True, exist_ok=True)
    gold.to_parquet(GOLD, index=False, compression="zstd")
    update_registry(f)
    return f, gold


def update_registry(f):
    cur = f[f.is_latest_version & (f.check_status == "OK")].sort_values("report_date").groupby("instrument_id").tail(1)
    cur = cur.assign(etf_aum=cur.net_assets_usd.where(cur.share_class_count <= 1))  # NULL when multi-class
    con = sqlite3.connect(REGISTRY)
    with con:
        for r in cur.itertuples():
            con.execute("""INSERT INTO etf_metadata (instrument_id, issuer, fund_name, aum, fund_domicile, updated_at)
                           VALUES (?, ?, ?, ?, 'US', ?)
                           ON CONFLICT(instrument_id) DO UPDATE SET issuer=excluded.issuer,
                             fund_name=excluded.fund_name, aum=excluded.aum,
                             fund_domicile=excluded.fund_domicile, updated_at=excluded.updated_at""",
                        (r.instrument_id, r.registrant, r.series_name, None if pd.isna(r.etf_aum) else r.etf_aum, now()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["download", "normalize", "all"], default="all")
    ap.add_argument("--pause", type=float, default=0.2)  # SEC fair access: <= 10 req/s
    a = ap.parse_args()
    if a.stage in ("download", "all"):
        u = universe(a.pause)
        print(u.filer_status.value_counts().to_string())
        man = download(u, a.pause)
        print("download:", man.download_status.value_counts().to_dict())
    if a.stage in ("normalize", "all"):
        f, gold = normalize()
        print("\nETF N-PORT SUMMARY")
        print("  filings:", len(f), f.check_status.value_counts().to_dict())
        print("  gold holdings rows:", len(gold), "| funds:", gold.series_id.nunique(),
              "| report dates:", gold.report_date.min().date(), "->", gold.report_date.max().date())


if __name__ == "__main__":
    main()

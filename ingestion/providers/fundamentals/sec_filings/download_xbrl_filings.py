"""Bulk download + normalize filing-level XBRL for every manifest filing with XBRL.

Input   : data/validation/fundamentals/sec_filings_manifest.csv (is_xbrl == 1)
Bronze  : data/bronze/fundamentals/sec_xbrl/cik=<CIK>/<accession>/<instance file>
Silver  : data/silver/fundamentals/filing_facts/cik=<CIK>/<accession>.parquet
Manifest: data/validation/fundamentals/sec_xbrl_manifest.csv
          accession, cik, ticker, cik_role, form, filing_date, report_date,
          source_url, instance_file, bytes, sha256, download_status,
          facts, silver_file, normalize_status, details

Instance selection from the filing's index.json:
  1. SEC-extracted inline-XBRL instance  (*_htm.xml)
  2. classic instance (*.xml that is not a linkbase/FilingSummary/schema)
SEC fair access: <= 10 requests/s; we use ~6/s. User-Agent per handover.
Restartable: existing Bronze and Silver files are not fetched/parsed again.

  python ingestion/providers/fundamentals/sec_filings/download_xbrl_filings.py [--stage download|normalize|all] [--limit N]
"""
import argparse
import hashlib
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "ingestion/providers/fundamentals"))
from xbrl_filing_normalizer import extract  # noqa: E402

FILINGS = ROOT / "data/validation/fundamentals/sec_filings_manifest.csv"
MANIFEST = ROOT / "data/validation/fundamentals/sec_xbrl_manifest.csv"
BRONZE = ROOT / "data/bronze/fundamentals/sec_xbrl"
SILVER = ROOT / "data/silver/fundamentals/filing_facts"
HEADERS = {"User-Agent": "FinancialDataLake/1.0 somdevsheel7@gmail.com",
           "Accept-Encoding": "gzip, deflate"}
EXCLUDE = ("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml", "filingsummary.xml")


def pick_instance(items):
    names = [i["name"] for i in items]
    htm = [n for n in names if n.lower().endswith("_htm.xml")]
    if htm:
        return htm[0]
    xml = [n for n in names if n.lower().endswith(".xml") and not n.lower().endswith(EXCLUDE)
           and not (n.upper().startswith("R") and n[1:-4].isdigit())]  # R1.xml... are rendered reports
    ins = [n for n in xml if "ins" in n.lower()]
    if ins:
        return ins[0]
    sized = sorted(((int(i.get("size") or 0), i["name"]) for i in items if i["name"] in xml), reverse=True)
    return sized[0][1] if sized else None


def load_manifest(filings):
    if MANIFEST.exists():
        return pd.read_csv(MANIFEST, dtype=str)
    base = filings[["accession_number", "cik", "ticker", "cik_role", "form", "filing_date", "report_date"]].copy()
    base = base.rename(columns={"accession_number": "accession"})
    for c in ["source_url", "instance_file", "bytes", "sha256", "download_status",
              "facts", "silver_file", "normalize_status", "details"]:
        base[c] = None
    return base


def save(m):
    tmp = MANIFEST.with_suffix(".tmp")
    m.to_csv(tmp, index=False)
    os.replace(tmp, MANIFEST)


def stage_download(m, limit, pause):
    s = requests.Session()
    s.headers.update(HEADERS)
    todo = m[m.download_status.isna() | m.download_status.isin(["FAILED"])].index
    if limit:
        todo = todo[:limit]
    for n, i in enumerate(todo, 1):
        r = m.loc[i]
        cik_int = str(int(r.cik))
        acc = r.accession.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/"
        try:
            idx = s.get(base + "index.json", timeout=45)
            time.sleep(pause)
            idx.raise_for_status()
            name = pick_instance(idx.json()["directory"]["item"])
            if not name:
                m.loc[i, ["download_status", "details"]] = ["NO_INSTANCE", "no instance document in filing index"]
                continue
            target = BRONZE / f"cik={r.cik}" / r.accession / name
            if not target.exists():
                resp = s.get(base + name, timeout=90)
                time.sleep(pause)
                resp.raise_for_status()
                if b"xbrl" not in resp.content[:5000].lower():
                    raise ValueError("payload is not an XBRL instance")
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".tmp")
                tmp.write_bytes(resp.content)
                os.replace(tmp, target)
            content = target.read_bytes()
            m.loc[i, ["source_url", "instance_file", "bytes", "sha256", "download_status", "details"]] = [
                base + name, str(target.relative_to(ROOT)), str(len(content)),
                hashlib.sha256(content).hexdigest(), "DOWNLOADED", None]
        except Exception as exc:  # noqa: BLE001
            m.loc[i, ["download_status", "details"]] = ["FAILED", str(exc)[:200]]
        if n % 100 == 0:
            save(m)
            print(f"download {n}/{len(todo)}", m.download_status.value_counts().to_dict(), flush=True)
    save(m)


def stage_normalize(m):
    todo = m[(m.download_status == "DOWNLOADED") & (m.normalize_status != "NORMALIZED")].index
    for n, i in enumerate(todo, 1):
        r = m.loc[i]
        out = SILVER / f"cik={r.cik}" / f"{r.accession}.parquet"
        try:
            df = extract(ROOT / r.instance_file, r.ticker, r.cik, r.accession)
            df["source_file"] = r.instance_file
            df["form"], df["filing_date"], df["report_date"], df["cik_role"] = r.form, r.filing_date, r.report_date, r.cik_role
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".tmp")
            df.to_parquet(tmp, index=False, compression="zstd")
            os.replace(tmp, out)
            m.loc[i, ["facts", "silver_file", "normalize_status"]] = [str(len(df)), str(out.relative_to(ROOT)), "NORMALIZED"]
        except Exception as exc:  # noqa: BLE001
            m.loc[i, ["normalize_status", "details"]] = ["FAILED", str(exc)[:200]]
        if n % 200 == 0:
            save(m)
            print(f"normalize {n}/{len(todo)}", flush=True)
    save(m)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=["download", "normalize", "all"], default="all")
    p.add_argument("--limit", type=int)
    p.add_argument("--pause", type=float, default=0.15)
    a = p.parse_args()
    filings = pd.read_csv(FILINGS, dtype=str)
    filings = filings[pd.to_numeric(filings.is_xbrl, errors="coerce") == 1]
    m = load_manifest(filings)
    new = filings[~filings.accession_number.isin(m.accession)]
    if not new.empty:  # filings added to the filings manifest since the last run
        add = new.rename(columns={"accession_number": "accession"})[
            ["accession", "cik", "ticker", "cik_role", "form", "filing_date", "report_date"]]
        m = pd.concat([m, add], ignore_index=True)
    if a.stage in ("download", "all"):
        stage_download(m, a.limit, a.pause)
    if a.stage in ("normalize", "all"):
        stage_normalize(m)
    print("\nSEC XBRL SUMMARY", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    print("Filings with XBRL:", len(m))
    print("Download:", m.download_status.value_counts(dropna=False).to_dict())
    print("Normalize:", m.normalize_status.value_counts(dropna=False).to_dict())
    print("Facts:", int(pd.to_numeric(m.facts, errors="coerce").sum()))


if __name__ == "__main__":
    main()

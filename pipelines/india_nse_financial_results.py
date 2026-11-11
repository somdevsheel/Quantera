"""Indian company fundamentals from NSE financial-results filings (XBRL).

Stages (each restartable; run in order, or --stage all):
  metadata  NSE API corporates-financial-results per symbol x {Quarterly, Annual}
            Bronze: data/bronze/india/fundamentals/nse_results/meta/<SYMBOL>_<period>.json
            Manifest: data/validation/india/nse_results_manifest.csv (one row per filing)
  xbrl      downloads each filing's XBRL instance (filings from ~2015 on carry one)
            Bronze: data/bronze/india/fundamentals/nse_results/xbrl/<SYMBOL>/<file>.xml
  normalize parses every XBRL instance into facts (all concepts kept, incl. custom /
            dimensional), then builds Gold key metrics from the primary context.
            Silver: data/silver/india/fundamentals/xbrl_facts/symbol=<SYMBOL>/<file>.parquet
            Gold  : data/gold/india/fundamentals/nse_key_financials.parquet
Universe: NIFTY 500 constituents first, then every other EQ/BE/SM/ST symbol in
data/silver/reference/india/nse_security_master_v2.parquet. --limit N restricts.
Logs: data/staging/india/nse_results_{metadata,xbrl}_log.csv

Fact model (same as SEC filing-level extractor): concept, namespace, context_id,
period_type, period_start, period_end/instant, dimensions (JSON), unit,
raw value, numeric value, decimals, nil flag. Dimensional facts are never summed.
Pre-2015 filings have metadata only (no XBRL) — documented gap.
"""
import argparse
import csv
import io
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "data/bronze/india/fundamentals/nse_results/meta"
XBRL = ROOT / "data/bronze/india/fundamentals/nse_results/xbrl"
FACTS = ROOT / "data/silver/india/fundamentals/xbrl_facts"
GOLD = ROOT / "data/gold/india/fundamentals/nse_key_financials.parquet"
MANIFEST = ROOT / "data/validation/india/nse_results_manifest.csv"
MASTER = ROOT / "data/silver/reference/india/nse_security_master_v2.parquet"
LOGDIR = ROOT / "data/staging/india"
PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"
API = "https://www.nseindia.com/api/corporates-financial-results"
N500 = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
           "Accept": "application/json, text/plain, */*", "Referer": PAGE}
XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
SCHEMA_VERSION = "nse_xbrl_facts_v1"

# Gold key metrics: first matching concept (local name) wins, primary context only.
KEY_METRICS = {
    "revenue_from_operations": ["RevenueFromOperations"],
    "other_income": ["OtherIncome"],
    "total_income": ["Income", "TotalIncome"],
    "total_expenses": ["Expenses", "TotalExpenses"],
    "finance_costs": ["FinanceCosts"],
    "depreciation_amortisation": ["DepreciationDepletionAndAmortisationExpense", "DepreciationAndAmortisationExpense"],
    "profit_before_tax": ["ProfitBeforeTax", "ProfitLossBeforeTax"],
    "tax_expense": ["TaxExpense"],
    "net_profit": ["ProfitLossForPeriod", "ProfitLossForThePeriod"],
    "net_profit_attributable_to_owners": ["ProfitOrLossAttributableToOwnersOfParent"],
    "eps_basic": ["BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
                  "BasicEarningsLossPerShareFromContinuingOperations", "BasicEarningsPerShare"],
    "eps_diluted": ["DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
                    "DilutedEarningsLossPerShareFromContinuingOperations", "DilutedEarningsPerShare"],
    "paid_up_equity_capital": ["PaidUpValueOfEquityShareCapital"],
    "face_value_per_share": ["FaceValueOfEquityShareCapital"],
}


class Nse:
    def __init__(self, pause):
        self.pause = pause
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.warm()

    def warm(self):
        try:
            self.s.get(PAGE, timeout=30)
        except requests.RequestException:
            pass

    def get(self, url, **kw):
        last = None
        for attempt in range(1, 5):
            try:
                r = self.s.get(url, timeout=(10, 90), **kw)
                if r.status_code in (401, 403):
                    self.warm()
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                if r.status_code == 404:
                    return r
                r.raise_for_status()
                return r
            except requests.RequestException as exc:
                last = exc
                time.sleep(min(60, 4 * 2 ** (attempt - 1)))
        raise last


def universe(limit):
    r = requests.get(N500, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com/"}, timeout=30)
    first = pd.read_csv(io.StringIO(r.text)).Symbol.str.strip().tolist() if r.ok else []
    m = pd.read_parquet(MASTER)
    rest = m[m.security_series.isin(["EQ", "BE", "SM", "ST"])].canonical_symbol.dropna().str.strip().tolist()
    syms = list(dict.fromkeys(first + rest))
    return syms[:limit] if limit else syms


def log_writer(name, fields):
    path = LOGDIR / f"nse_results_{name}_log.csv"
    new = not path.exists()
    fh = path.open("a", newline="")
    w = csv.DictWriter(fh, fieldnames=fields)
    if new:
        w.writeheader()
    return fh, w


def stage_metadata(symbols, nse, refresh):
    META.mkdir(parents=True, exist_ok=True)
    fh, w = log_writer("metadata", ["symbol", "period", "status", "filings", "details", "logged_utc"])
    for i, sym in enumerate(symbols, 1):
        for period in ("Quarterly", "Annual"):
            target = META / f"{re.sub(r'[^A-Za-z0-9&_-]', '_', sym)}_{period}.json"
            if target.exists() and not refresh:
                continue
            row = {"symbol": sym, "period": period, "logged_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            try:
                r = nse.get(API, params={"index": "equities", "period": period, "symbol": sym})
                data = r.json() if r.status_code == 200 else []
                if not isinstance(data, list):
                    raise ValueError("unexpected payload")
                tmp = target.with_suffix(".tmp")
                tmp.write_text(json.dumps(data))
                os.replace(tmp, target)
                row.update(status="DOWNLOADED", filings=len(data))
            except Exception as exc:  # noqa: BLE001
                row.update(status="FAILED", details=str(exc)[:200])
            w.writerow(row)
            fh.flush()
            time.sleep(nse.pause)
        if i % 50 == 0:
            print(f"metadata {i}/{len(symbols)}", flush=True)
    fh.close()
    build_manifest()


def build_manifest():
    rows = []
    for f in sorted(META.glob("*.json")):
        for rec in json.loads(f.read_text()):
            x = rec.get("xbrl") or ""
            rows.append({
                "symbol": rec.get("symbol"), "isin": rec.get("isin"), "company_name": rec.get("companyName"),
                "period_type": rec.get("period"), "relating_to": rec.get("relatingTo"),
                "from_date": rec.get("fromDate"), "to_date": rec.get("toDate"),
                "financial_year": rec.get("financialYear"), "consolidated": rec.get("consolidated"),
                "audited": rec.get("audited"), "cumulative": rec.get("cumulative"),
                "ind_as": rec.get("indAs"), "bank": rec.get("bank"),
                "filing_datetime": rec.get("filingDate"), "broadcast_datetime": rec.get("broadCastDate"),
                "seq_number": rec.get("seqNumber"), "xbrl_url": x if x.lower().endswith(".xml") else None,
                "meta_file": str(f.relative_to(ROOT)),
            })
    m = pd.DataFrame(rows)
    if m.empty:
        return m
    m = m.drop_duplicates(subset=["symbol", "seq_number", "period_type", "xbrl_url"])
    m["xbrl_file"] = m.apply(lambda r: str((XBRL / re.sub(r"[^A-Za-z0-9&_-]", "_", r.symbol)
                                            / r.xbrl_url.rsplit("/", 1)[-1]).relative_to(ROOT))
                             if isinstance(r.xbrl_url, str) else None, axis=1)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    m.to_csv(MANIFEST, index=False)
    print("Manifest filings:", len(m), "| with XBRL:", int(m.xbrl_url.notna().sum()),
          "| symbols:", m.symbol.nunique())
    return m


def stage_xbrl(symbols, nse):
    m = pd.read_csv(MANIFEST, dtype=str)
    order = {s: i for i, s in enumerate(symbols)}
    m = m[m.xbrl_url.notna() & m.symbol.isin(order)]
    m = m.assign(o=m.symbol.map(order)).sort_values(["o", "to_date"]).drop_duplicates("xbrl_file")
    fh, w = log_writer("xbrl", ["symbol", "xbrl_file", "status", "bytes", "details", "logged_utc"])
    done = 0
    for r in m.itertuples():
        target = ROOT / r.xbrl_file
        if target.exists() and target.stat().st_size > 0:
            continue
        row = {"symbol": r.symbol, "xbrl_file": r.xbrl_file,
               "logged_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        try:
            resp = nse.get(r.xbrl_url)
            if resp.status_code == 404:
                row.update(status="UNAVAILABLE", details="HTTP 404")
            elif b"<xbrl" not in resp.content[:2000] and b":xbrl" not in resp.content[:2000]:
                row.update(status="FAILED", details="not an XBRL instance")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(".tmp")
                tmp.write_bytes(resp.content)
                os.replace(tmp, target)
                row.update(status="DOWNLOADED", bytes=len(resp.content))
        except Exception as exc:  # noqa: BLE001
            row.update(status="FAILED", details=str(exc)[:200])
        w.writerow(row)
        fh.flush()
        done += 1
        if done % 200 == 0:
            print(f"xbrl downloaded {done}", flush=True)
        time.sleep(nse.pause)
    fh.close()


def parse_instance(path):
    tree = etree.parse(str(path), etree.XMLParser(huge_tree=True, recover=True))
    root = tree.getroot()
    contexts, units = {}, {}
    for c in root.iter(f"{{{XBRLI}}}context"):
        p = c.find(f"{{{XBRLI}}}period")
        inst = p.findtext(f"{{{XBRLI}}}instant")
        dims = {}
        for mem in c.iter(f"{{{XBRLDI}}}explicitMember"):
            dims[mem.get("dimension")] = (mem.text or "").strip()
        for mem in c.iter(f"{{{XBRLDI}}}typedMember"):
            dims[mem.get("dimension")] = "".join(mem.itertext()).strip()
        contexts[c.get("id")] = {
            "period_type": "instant" if inst else "duration",
            "period_start": p.findtext(f"{{{XBRLI}}}startDate"),
            "period_end": inst or p.findtext(f"{{{XBRLI}}}endDate"),
            "dimensions": json.dumps(dims, sort_keys=True) if dims else None,
            "dimension_count": len(dims),
        }
    for u in root.iter(f"{{{XBRLI}}}unit"):
        units[u.get("id")] = " ".join(t.strip() for t in u.itertext() if t.strip())
    facts = []
    for el in root:
        if not isinstance(el.tag, str) or el.get("contextRef") is None:
            continue
        ns, local = el.tag[1:].split("}", 1)
        ctx = contexts.get(el.get("contextRef"), {})
        raw = (el.text or "").strip()
        nil = el.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true"
        try:
            num = float(raw) if raw and el.get("unitRef") else None
        except ValueError:
            num = None
        facts.append({"concept": local, "namespace": ns, "context_id": el.get("contextRef"),
                      "context_defined": bool(ctx),
                      **{k: ctx.get(k) for k in ("period_type", "period_start", "period_end",
                                                 "dimensions", "dimension_count")},
                      "period_source": "CONTEXT" if ctx else None,
                      "unit": units.get(el.get("unitRef")), "decimals": el.get("decimals"),
                      "raw_value": raw[:4000], "numeric_value": num, "is_nil": nil})
    df = pd.DataFrame(facts)
    if df.empty:
        return df
    # Position in the instance; identical repeats (XBRL "consistent duplicates": same
    # concept, context, unit, value) are kept but flagged so consumers can drop them.
    df["fact_seq"] = range(len(df))
    df["is_duplicate_fact"] = df.duplicated(["concept", "namespace", "context_id", "unit", "raw_value"])
    # Some filings reference contexts they never define (e.g. "OneD"). Their period is
    # then taken from the instance's own reporting-period facts and flagged.
    undefined = ~df.context_defined
    if undefined.any():
        def fact(name):
            v = df.loc[df.concept == name, "raw_value"]
            return v.iloc[0] if not v.empty else None
        start, end = fact("DateOfStartOfReportingPeriod"), fact("DateOfEndOfReportingPeriod")
        fy_start = fact("DateOfStartOfFinancialYear")
        # SEBI results layout: OneD = current reporting period, FourD = year to date.
        known = {"OneD": (start, "INSTANCE_REPORTING_PERIOD_FACTS"),
                 "FourD": (fy_start, "INSTANCE_FY_TO_DATE_FACTS")}
        for cid, (pstart, source) in known.items():
            if not end:  # filing never states its period -> stays UNRESOLVED_CONTEXT
                continue
            sel = undefined & (df.context_id == cid)
            df.loc[sel, ["period_type", "period_start", "period_end"]] = ["duration", pstart, end]
            df.loc[undefined & (df.context_id == cid), "period_source"] = source
        df.loc[undefined, "dimension_count"] = 0
        df.loc[undefined & df.period_source.isna(), "period_source"] = "UNRESOLVED_CONTEXT"
    # Filers sometimes mis-date a context (e.g. FourD carrying quarter dates in an
    # annual filing). When a context states its own reporting period via
    # DateOfStart/EndOfReportingPeriod facts, that statement wins for duration facts.
    df["context_period_start"], df["context_period_end"] = df.period_start, df.period_end
    stated = df[df.concept.isin(["DateOfStartOfReportingPeriod", "DateOfEndOfReportingPeriod"])]
    stated = stated.pivot_table(index="context_id", columns="concept", values="raw_value", aggfunc="first")
    for cid, row in stated.iterrows():
        st, en = row.get("DateOfStartOfReportingPeriod"), row.get("DateOfEndOfReportingPeriod")
        if not (isinstance(st, str) and isinstance(en, str)):
            continue
        sel = (df.context_id == cid) & (df.period_type == "duration")
        changed = sel & ((df.period_start != st) | (df.period_end != en))
        df.loc[sel, ["period_start", "period_end"]] = [st, en]
        df.loc[changed, "period_source"] = "CONTEXT_REPORTING_PERIOD_FACTS"
    return df


def stage_normalize(symbols, reparse=False):
    m = pd.read_csv(MANIFEST, dtype=str)
    m = m[m.xbrl_file.notna() & m.symbol.isin(set(symbols))]
    # NSE sometimes lists the same XBRL document under two filing entries; one row per document
    # (same rule as stage_xbrl).
    m = m.drop_duplicates("xbrl_file", keep="last")
    gold_rows, parsed, failed = [], 0, 0
    for r in m.itertuples():
        src = ROOT / r.xbrl_file
        if not src.exists():
            continue
        out = FACTS / f"symbol={re.sub(r'[^A-Za-z0-9&_-]', '_', r.symbol)}" / (src.stem + ".parquet")
        try:
            if out.exists() and not reparse:
                f = pd.read_parquet(out)
            else:
                f = parse_instance(src)
                for k in ("symbol", "isin", "period_type", "from_date", "to_date", "consolidated",
                          "audited", "filing_datetime", "seq_number"):
                    f["filing_" + k if k in ("period_type",) else k] = getattr(r, k)
                f["source_file"] = r.xbrl_file
                f["schema_version"] = SCHEMA_VERSION
                out.parent.mkdir(parents=True, exist_ok=True)
                tmp = out.with_name(out.name + ".tmp")
                f.to_parquet(tmp, index=False, compression="zstd")
                os.replace(tmp, out)
                parsed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("PARSE FAILED", r.xbrl_file, exc)
            continue
        # Primary context: no dimensions; duration ending at the filing's to_date.
        to_date = pd.to_datetime(r.to_date, format="%d-%b-%Y", errors="coerce")
        prim = f[(f.dimension_count == 0) & f.numeric_value.notna()]
        end = pd.to_datetime(prim.period_end, errors="coerce")
        prim = prim[end == to_date] if pd.notna(to_date) else prim
        g = {"period_source": prim.period_source.iloc[0] if not prim.empty else None,
             "symbol": r.symbol, "isin": r.isin, "period_type": r.period_type, "from_date": r.from_date,
             "to_date": r.to_date, "consolidated": r.consolidated, "audited": r.audited,
             "filing_datetime": r.filing_datetime, "source_file": r.xbrl_file}
        for metric, names in KEY_METRICS.items():
            val = None
            for n in names:
                hit = prim[prim.concept == n]
                if not hit.empty:
                    # Duration facts for the filing period; prefer the one starting at from_date.
                    frm = pd.to_datetime(r.from_date, format="%d-%b-%Y", errors="coerce")
                    exact = hit[pd.to_datetime(hit.period_start, errors="coerce") == frm]
                    if not exact.empty:  # never fall back to another period (e.g. Q4 for Annual)
                        val = exact.numeric_value.iloc[0]
                        break
            g[metric] = val
        gold_rows.append(g)
    gold = pd.DataFrame(gold_rows)
    if not gold.empty:
        for c in ("from_date", "to_date"):
            gold[c] = pd.to_datetime(gold[c], format="%d-%b-%Y", errors="coerce")
        gold["filing_datetime"] = pd.to_datetime(gold.filing_datetime, format="%d-%b-%Y %H:%M", errors="coerce")
        # Revisions: several filings can cover the same period; keep all, flag the latest.
        key = ["symbol", "period_type", "from_date", "to_date", "consolidated"]
        gold = gold.sort_values(key + ["filing_datetime", "source_file"])
        gold["is_latest_filing"] = ~gold.duplicated(key, keep="last")
        gold["filings_for_period"] = gold.groupby(key, dropna=False).source_file.transform("count")
        gold["currency"] = "INR"
        gold["units_note"] = "monetary values as reported in XBRL (INR); EPS in INR per share"
        GOLD.parent.mkdir(parents=True, exist_ok=True)
        gold.to_parquet(GOLD, index=False, compression="zstd")
    print(f"Normalize: parsed {parsed} new filings, failed {failed}, gold rows {len(gold)}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stage", choices=["metadata", "xbrl", "normalize", "all"], default="all")
    p.add_argument("--limit", type=int)
    p.add_argument("--pause", type=float, default=1.0)
    p.add_argument("--refresh-metadata", action="store_true")
    p.add_argument("--reparse", action="store_true", help="re-parse XBRL even if Silver facts exist")
    a = p.parse_args()
    symbols = universe(a.limit)
    print("Universe:", len(symbols), "symbols", flush=True)
    nse = Nse(a.pause) if a.stage in ("metadata", "xbrl", "all") else None
    if a.stage in ("metadata", "all"):
        stage_metadata(symbols, nse, a.refresh_metadata)
    if a.stage in ("xbrl", "all"):
        stage_xbrl(symbols, nse)
    if a.stage in ("normalize", "all"):
        stage_normalize(symbols, a.reparse)


if __name__ == "__main__":
    main()

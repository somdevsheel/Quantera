"""Enrich the SEC filings manifest with XBRL availability and a form class.

Adds: is_xbrl, is_inline_xbrl, acceptance_datetime, form_class.
Main submissions JSON per CIK is stored in Bronze (fetched once); archive
JSONs already in Bronze are reused. Restartable and idempotent.

form_class:
  PERIODIC_STATEMENTS  10-K / 10-Q / 20-F / 40-F (contain financial statements)
  CURRENT_REPORT       6-K (disclosure; financial statements only sometimes,
                       XBRL presence is the signal, not the form type)
"""
from pathlib import Path
import json
import time

import pandas as pd
import requests

MANIFEST = Path("data/validation/fundamentals/sec_filings_manifest.csv")
BRONZE = Path("data/bronze/fundamentals/sec_submissions")
BASE = "https://data.sec.gov"
HEADERS = {
    "User-Agent": "FinancialDataLake/1.0 somdevsheel7@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}
PERIODIC = {"10-K", "10-Q", "20-F", "40-F"}


def load_blocks(cik, session):
    main_path = BRONZE / f"CIK{cik}.json"
    if not main_path.exists():
        resp = session.get(f"{BASE}/submissions/CIK{cik}.json", timeout=45)
        resp.raise_for_status()
        main_path.write_bytes(resp.content)
        time.sleep(0.15)
    data = json.loads(main_path.read_text())
    blocks = [data["filings"]["recent"]]
    for archive in data["filings"].get("files", []):
        path = BRONZE / archive["name"]
        if not path.exists():
            resp = session.get(f"{BASE}/submissions/{archive['name']}", timeout=45)
            resp.raise_for_status()
            path.write_bytes(resp.content)
            time.sleep(0.15)
        blocks.append(json.loads(path.read_text()))
    return blocks


def main():
    BRONZE.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(HEADERS)

    manifest = pd.read_csv(MANIFEST, dtype={"cik": "string", "successor_cik": "string"})
    info = {}
    failures = []

    for cik in sorted(manifest.cik.dropna().unique()):
        try:
            for block in load_blocks(cik, session):
                acc = block.get("accessionNumber", [])
                for i, a in enumerate(acc):
                    def f(k):
                        v = block.get(k, [])
                        return v[i] if i < len(v) else None
                    info[(cik, a)] = (f("isXBRL"), f("isInlineXBRL"), f("acceptanceDateTime"))
        except Exception as exc:  # noqa: BLE001
            failures.append((cik, str(exc)))
            print("FAILED", cik, exc)

    keys = list(zip(manifest.cik, manifest.accession_number))
    vals = [info.get(k, (None, None, None)) for k in keys]
    manifest["is_xbrl"] = [v[0] for v in vals]
    manifest["is_inline_xbrl"] = [v[1] for v in vals]
    manifest["acceptance_datetime"] = [v[2] for v in vals]
    manifest["form_class"] = manifest.form.map(
        lambda f: "PERIODIC_STATEMENTS" if f in PERIODIC else "CURRENT_REPORT"
    )
    manifest.to_csv(MANIFEST, index=False)

    unmatched = sum(v[0] is None for v in vals)
    print("Rows:", len(manifest), "| unmatched:", unmatched, "| CIK failures:", len(failures))
    print(pd.crosstab(manifest.form, manifest.is_xbrl.astype(str)))


if __name__ == "__main__":
    main()

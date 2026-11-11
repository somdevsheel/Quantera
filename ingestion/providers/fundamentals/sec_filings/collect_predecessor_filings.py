"""Collect SEC filings filed under predecessor CIKs and link them to the
current canonical ticker.

Rows keep the filer's own CIK in `cik` (source lineage is not rewritten);
`cik_role` = "PREDECESSOR" and `successor_cik` identify the link.
Idempotent: keyed on (cik, accession_number).
"""
from pathlib import Path
import time

import pandas as pd
import requests

PREDECESSORS = Path("reference/universes/companies/sec_predecessor_ciks.csv")
MANIFEST = Path("data/validation/fundamentals/sec_filings_manifest.csv")
ARCHIVE_DIR = Path("data/bronze/fundamentals/sec_submissions")

BASE = "https://data.sec.gov"
HEADERS = {
    "User-Agent": "FinancialDataLake/1.0 somdevsheel7@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}
FORMS = {"10-K", "10-Q", "20-F", "40-F", "6-K"}


def rows_from(block, name, ticker, cik, successor, source):
    forms = block.get("form", [])
    for i, form in enumerate(forms):
        if form not in FORMS:
            continue

        def field(key):
            values = block.get(key, [])
            return values[i] if i < len(values) else None

        yield {
            "ticker": ticker,
            "cik": cik,
            "company_name": name,
            "form": form,
            "filing_date": field("filingDate"),
            "report_date": field("reportDate"),
            "accession_number": field("accessionNumber"),
            "primary_document": field("primaryDocument"),
            "primary_doc_description": field("primaryDocDescription"),
            "source": source,
            "status": "DISCOVERED",
            "cik_role": "PREDECESSOR",
            "successor_cik": successor,
        }


def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    links = pd.read_csv(PREDECESSORS, dtype=str)
    manifest = pd.read_csv(MANIFEST, dtype={"cik": "string"})
    if "cik_role" not in manifest:
        manifest["cik_role"] = "CURRENT"
        manifest["successor_cik"] = pd.NA
    existing = set(zip(manifest.cik.astype(str), manifest.accession_number))

    new_rows, failures = [], []
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    for link in links.itertuples():
        ticker, cik, successor = link.canonical_symbol, link.predecessor_cik, link.current_cik
        try:
            main_json = session.get(f"{BASE}/submissions/CIK{cik}.json", timeout=45)
            main_json.raise_for_status()
            (ARCHIVE_DIR / f"CIK{cik}.json").write_bytes(main_json.content)
            data = main_json.json()
            name = data.get("name")

            blocks = [(data["filings"]["recent"], "SEC submissions API")]
            for archive in data["filings"].get("files", []):
                time.sleep(0.15)
                resp = session.get(f"{BASE}/submissions/{archive['name']}", timeout=45)
                resp.raise_for_status()
                (ARCHIVE_DIR / archive["name"]).write_bytes(resp.content)
                blocks.append((resp.json(), "SEC submissions archive"))

            added = 0
            for block, source in blocks:
                for row in rows_from(block, name, ticker, cik, successor, source):
                    key = (cik, row["accession_number"])
                    if key in existing:
                        continue
                    existing.add(key)
                    new_rows.append(row)
                    added += 1
            print(f"SUCCESS {ticker} <- {cik} ({name}): {len(blocks) - 1} archives, {added} new filings")
        except Exception as exc:  # noqa: BLE001
            failures.append({"ticker": ticker, "cik": cik, "error": str(exc)})
            print(f"FAILED {ticker} <- {cik}: {exc}")
        time.sleep(0.2)

    if new_rows:
        manifest = pd.concat([manifest, pd.DataFrame(new_rows)], ignore_index=True)
    manifest["cik_role"] = manifest["cik_role"].fillna("CURRENT")
    manifest = manifest.drop_duplicates(subset=["cik", "accession_number"], keep="first")
    manifest.to_csv(MANIFEST, index=False)

    print("\nPredecessor collection complete")
    print("New filings:", len(new_rows))
    print("Total filings:", len(manifest))
    print("Failures:", len(failures))
    if failures:
        pd.DataFrame(failures).to_csv(
            "data/validation/fundamentals/sec_predecessor_failures.csv", index=False
        )


if __name__ == "__main__":
    main()

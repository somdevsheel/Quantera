from pathlib import Path
import json
import time

import pandas as pd
import requests

UNIVERSE = Path("reference/universes/companies/global_company_universe.csv")
OUTPUT = Path("data/validation/fundamentals/sec_filings_manifest.csv")

BASE_URL = "https://data.sec.gov"
HEADERS = {
    "User-Agent": "FinancialDataLake/1.0 somdevsheel7@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

FORMS = {"10-K", "10-Q", "20-F", "6-K", "40-F"}
session = requests.Session()
session.headers.update(HEADERS)

companies = pd.read_csv(UNIVERSE, dtype={"sec_cik": "string"})
companies = companies[
    (companies.country == "US")
    & companies.sec_cik.str.fullmatch(r"\d{10}", na=False)
]

OUTPUT.parent.mkdir(parents=True, exist_ok=True)

if OUTPUT.exists():
    manifest = pd.read_csv(OUTPUT, dtype={"cik": "string"})
else:
    manifest = pd.DataFrame()

existing = set()
if not manifest.empty:
    existing = set(
        zip(manifest["cik"].astype(str), manifest["accession_number"])
    )

new_rows = []
failures = []

for _, company in companies.iterrows():
    ticker = company["canonical_symbol"]
    cik = company["sec_cik"].zfill(10)

    url = f"{BASE_URL}/submissions/CIK{cik}.json"

    try:
        response = session.get(url, timeout=45)
        response.raise_for_status()
        data = response.json()

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])

        for i, form in enumerate(forms):
            if form not in FORMS:
                continue

            accession = recent["accessionNumber"][i]
            key = (cik, accession)

            if key in existing:
                continue

            new_rows.append({
                "ticker": ticker,
                "cik": cik,
                "company_name": data.get("name"),
                "form": form,
                "filing_date": recent["filingDate"][i],
                "report_date": recent["reportDate"][i],
                "accession_number": accession,
                "primary_document": recent["primaryDocument"][i],
                "primary_doc_description": (
                    recent.get("primaryDocDescription", [None] * len(forms))[i]
                ),
                "source": "SEC submissions API",
                "status": "DISCOVERED",
            })
            existing.add(key)

        print(f"SUCCESS {ticker}: checked {len(forms)} recent filings")

    except Exception as exc:
        failures.append({"ticker": ticker, "cik": cik, "error": str(exc)})
        print(f"FAILED {ticker}: {exc}")

    time.sleep(0.15)

if new_rows:
    new_df = pd.DataFrame(new_rows)
    manifest = pd.concat([manifest, new_df], ignore_index=True)
    manifest = manifest.drop_duplicates(
        subset=["cik", "accession_number"], keep="last"
    )
    manifest.to_csv(OUTPUT, index=False)

print("\nDiscovery complete")
print("Companies:", len(companies))
print("New filings:", len(new_rows))
print("Total manifest filings:", len(manifest))
print("Failures:", len(failures))
print("Manifest:", OUTPUT)

if failures:
    failure_path = OUTPUT.with_name("sec_discovery_failures.csv")
    pd.DataFrame(failures).to_csv(failure_path, index=False)
    print("Failure report:", failure_path)

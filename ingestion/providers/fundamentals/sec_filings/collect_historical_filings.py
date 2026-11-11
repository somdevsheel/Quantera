from pathlib import Path
import time
import requests
import pandas as pd

UNIVERSE = Path("reference/universes/companies/global_company_universe.csv")
MANIFEST = Path("data/validation/fundamentals/sec_filings_manifest.csv")

OUTPUT = Path("data/validation/fundamentals/sec_filings_manifest.csv")
ARCHIVE_DIR = Path("data/bronze/fundamentals/sec_submissions")

BASE = "https://data.sec.gov"
HEADERS = {
    "User-Agent": "FinancialDataLake/1.0 somdevsheel7@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

FORMS = {"10-K", "10-Q", "20-F", "40-F", "6-K"}
session = requests.Session()
session.headers.update(HEADERS)

companies = pd.read_csv(UNIVERSE, dtype={"sec_cik": "string"})
companies = companies[
    (companies.country == "US")
    & companies.sec_cik.str.fullmatch(r"\d{10}", na=False)
]

manifest = pd.read_csv(MANIFEST, dtype={"cik": "string"})
existing = set(zip(manifest.cik.astype(str), manifest.accession_number))

new_rows = []
failures = []

def add_filings(data, ticker, cik):
    # Main submissions use filings.recent.
    # Historical archive files contain arrays at the top level.
    recent = data.get("filings", {}).get("recent", data)

    forms = recent.get("form", [])
    count = 0

    for i, form in enumerate(forms):
        if form not in FORMS:
            continue

        accession = recent["accessionNumber"][i]
        key = (cik, accession)

        if key in existing:
            continue

        def field(name):
            values = recent.get(name, [])
            return values[i] if i < len(values) else None

        new_rows.append({
            "ticker": ticker,
            "cik": cik,
            "company_name": data.get("name"),
            "form": form,
            "filing_date": field("filingDate"),
            "report_date": field("reportDate"),
            "accession_number": accession,
            "primary_document": field("primaryDocument"),
            "primary_doc_description": field("primaryDocDescription"),
            "source": "SEC submissions archive",
            "status": "DISCOVERED",
        })

        existing.add(key)
        count += 1

    return count

for _, company in companies.iterrows():
    ticker = company["canonical_symbol"]
    cik = company["sec_cik"].zfill(10)

    try:
        url = f"{BASE}/submissions/CIK{cik}.json"
        response = session.get(url, timeout=45)
        response.raise_for_status()
        data = response.json()

        added = add_filings(data, ticker, cik)

        archive_files = data.get("filings", {}).get("files", [])

        for archive in archive_files:
            archive_url = f"{BASE}/submissions/{archive['name']}"
            archive_response = session.get(archive_url, timeout=45)
            archive_response.raise_for_status()

            archive_data = archive_response.json()

            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            archive_path = ARCHIVE_DIR / archive["name"]
            archive_path.write_bytes(archive_response.content)

            added += add_filings(archive_data, ticker, cik)
            time.sleep(0.12)

        print(
            f"SUCCESS {ticker}: "
            f"{len(archive_files)} archives, {added} new filings"
        )

    except Exception as exc:
        failures.append({
            "ticker": ticker,
            "cik": cik,
            "error": str(exc)
        })
        print(f"FAILED {ticker}: {exc}")

    time.sleep(0.15)

if new_rows:
    manifest = pd.concat(
        [manifest, pd.DataFrame(new_rows)],
        ignore_index=True
    )

manifest = manifest.drop_duplicates(
    subset=["cik", "accession_number"],
    keep="last"
)

manifest.to_csv(OUTPUT, index=False)

print("\nHistorical collection complete")
print("Companies:", len(companies))
print("New filings:", len(new_rows))
print("Total filings:", len(manifest))
print("Companies covered:", manifest.ticker.nunique())
print("Failures:", len(failures))

if failures:
    pd.DataFrame(failures).to_csv(
        "data/validation/fundamentals/sec_archive_failures.csv",
        index=False
    )

print("Manifest:", OUTPUT)

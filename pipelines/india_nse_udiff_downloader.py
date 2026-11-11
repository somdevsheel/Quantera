import argparse
import io
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import requests

OUT = Path("data/staging/india/historical")
BASE = "https://nsearchives.nseindia.com/content/cm/"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
    "Referer": "https://www.nseindia.com/all-reports",
}

def dates(start, end):
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if start < date(2024, 7, 8) or end < start:
        raise SystemExit("Use dates from 2024-07-08 onward.")

    OUT.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(HEADERS)
    session.get("https://www.nseindia.com/all-reports", timeout=30)

    downloaded = skipped = unavailable = failed = 0

    for day in dates(start, end):
        name = f"nse_bhavcopy_{day:%Y%m%d}.zip"
        path = OUT / name

        if path.exists():
            skipped += 1
            continue

        url = (
            BASE
            + f"BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
        )

        for attempt in range(3):
            try:
                response = session.get(url, timeout=45)

                if response.status_code in (403, 404):
                    unavailable += 1
                    break

                response.raise_for_status()

                if not zipfile.is_zipfile(io.BytesIO(response.content)):
                    raise ValueError("Response is not a valid ZIP")

                path.write_bytes(response.content)
                downloaded += 1
                print("DOWNLOADED", day, len(response.content))
                break

            except Exception as exc:
                if attempt == 2:
                    failed += 1
                    print("FAILED", day, str(exc))
                else:
                    time.sleep(2 ** attempt)

        time.sleep(0.7)

    print("\nUDiFF DOWNLOAD SUMMARY")
    print("Downloaded:", downloaded)
    print("Already present:", skipped)
    print("Unavailable:", unavailable)
    print("Failed:", failed)

if __name__ == "__main__":
    main()

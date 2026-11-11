import argparse
import time
import zipfile
from pathlib import Path
from datetime import datetime, timedelta

import requests

OUT = Path("data/staging/india/historical")
OUT.mkdir(parents=True, exist_ok=True)

BASE = "https://archives.nseindia.com/content/historical/EQUITIES"

parser = argparse.ArgumentParser()
parser.add_argument("--start", required=True, help="YYYY-MM-DD")
parser.add_argument("--end", required=True, help="YYYY-MM-DD")
args = parser.parse_args()

start = datetime.strptime(args.start, "%Y-%m-%d").date()
end = datetime.strptime(args.end, "%Y-%m-%d").date()

if end < start:
    raise SystemExit("End date must not precede start date")

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Referer": "https://www.nseindia.com/all-reports",
})

home = session.get("https://www.nseindia.com/all-reports", timeout=30)
home.raise_for_status()

downloaded = skipped = unavailable = failed = 0
day = start

while day <= end:
    if day.weekday() >= 5:
        skipped += 1
        day += timedelta(days=1)
        continue

    stamp = day.strftime("%Y%m%d")
    target = OUT / f"nse_legacy_bhavcopy_{stamp}.zip"

    if target.exists():
        skipped += 1
        day += timedelta(days=1)
        continue

    month = day.strftime("%b").upper()
    filename = f"cm{day.strftime('%d')}{month}{day.year}bhav.csv.zip"
    url = f"{BASE}/{day.year}/{month}/{filename}"

    success = False

    for attempt in range(3):
        try:
            response = session.get(url, timeout=45)
            if response.status_code == 200:
                content = response.content
                if zipfile.is_zipfile(__import__("io").BytesIO(content)):
                    target.write_bytes(content)
                    downloaded += 1
                    success = True
                    print(f"DOWNLOADED {stamp} ({len(content)} bytes)")
                else:
                    failed += 1
                    print(f"INVALID ZIP {stamp}")
                break

            if response.status_code in (403, 404):
                unavailable += 1
                print(f"UNAVAILABLE {stamp}: HTTP {response.status_code}")
                break

            if response.status_code == 429 or response.status_code >= 500:
                time.sleep(3 * (attempt + 1))
                continue

            failed += 1
            print(f"FAILED {stamp}: HTTP {response.status_code}")
            break

        except requests.RequestException as exc:
            if attempt == 2:
                failed += 1
                print(f"ERROR {stamp}: {exc}")
            else:
                time.sleep(3 * (attempt + 1))

    time.sleep(0.7)
    day += timedelta(days=1)

print("\nDOWNLOAD SUMMARY")
print("Downloaded:", downloaded)
print("Skipped:", skipped)
print("Unavailable:", unavailable)
print("Failed:", failed)

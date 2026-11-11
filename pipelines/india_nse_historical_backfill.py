import argparse
import calendar
import csv
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(".")
DOWNLOADER = ROOT / "pipelines/india_nse_historical_downloader.py"
NORMALIZER = ROOT / "pipelines/india_nse_legacy_normalizer.py"
STAGING = ROOT / "data/staging/india/historical"
SILVER = ROOT / "data/silver/india/equity_prices"
LOG = ROOT / "data/staging/india/historical_backfill_log.csv"

parser = argparse.ArgumentParser()
parser.add_argument("--start", default="2000-01-01")
parser.add_argument("--end", default="2024-07-05")
args = parser.parse_args()

start = date.fromisoformat(args.start)
end = date.fromisoformat(args.end)

if end < start:
    raise SystemExit("End date must not precede start date")

fields = ["date", "download_status", "normalization_status", "details"]
if not LOG.exists():
    with LOG.open("w", newline="") as f:
        csv.DictWriter(f, fieldnames=fields).writeheader()

def log(day, download, normalization, details=""):
    with LOG.open("a", newline="") as f:
        csv.DictWriter(f, fieldnames=fields).writerow({
            "date": day.isoformat(),
            "download_status": download,
            "normalization_status": normalization,
            "details": details,
        })

month = date(start.year, start.month, 1)

while month <= end:
    last_day = calendar.monthrange(month.year, month.month)[1]
    month_end = date(month.year, month.month, last_day)
    batch_start = max(start, month)
    batch_end = min(end, month_end)

    print(f"\n===== {batch_start} to {batch_end} =====", flush=True)

    result = subprocess.run([
        sys.executable, str(DOWNLOADER),
        "--start", batch_start.isoformat(),
        "--end", batch_end.isoformat()
    ])

    if result.returncode != 0:
        print("Downloader failed. Stopping for safe resume.")
        break

    day = batch_start
    while day <= batch_end:
        if day.weekday() < 5:
            stamp = day.strftime("%Y%m%d")
            archive = STAGING / f"nse_legacy_bhavcopy_{stamp}.zip"
            output = SILVER / f"nse_legacy_equity_prices_{stamp}.parquet"

            if output.exists():
                log(day, "EXISTING", "EXISTING")
            elif not archive.exists():
                log(day, "UNAVAILABLE", "NOT_RUN")
            else:
                normalized = subprocess.run([
                    sys.executable, str(NORMALIZER), str(archive)
                ], capture_output=True, text=True)

                if normalized.returncode == 0 and output.exists():
                    log(day, "DOWNLOADED_OR_EXISTING", "SUCCESS")
                    print(f"NORMALIZED {stamp}", flush=True)
                else:
                    log(day, "DOWNLOADED_OR_EXISTING", "FAILED",
                        normalized.stderr[-500:])
                    print(f"NORMALIZATION FAILED {stamp}", flush=True)

        day = date.fromordinal(day.toordinal() + 1)

    month = date(month.year + (month.month == 12),
                 1 if month.month == 12 else month.month + 1, 1)

print(f"\nBackfill log: {LOG}")

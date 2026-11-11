import argparse
import calendar
import csv
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(".")
ARCHIVES = Path("data/staging/india/historical")
SILVER = Path("data/silver/india/equity_prices")
LOG = ARCHIVES / "udiff_backfill_log.csv"
DOWNLOADER = "pipelines/india_nse_udiff_downloader.py"
NORMALIZER = "pipelines/india_nse_udiff_normalizer.py"

def month_end(day):
    last = calendar.monthrange(day.year, day.month)[1]
    return date(day.year, day.month, last)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    if start < date(2024, 7, 8) or end < start:
        raise SystemExit("Invalid UDiFF date range.")

    ARCHIVES.mkdir(parents=True, exist_ok=True)
    SILVER.mkdir(parents=True, exist_ok=True)

    new_log = not LOG.exists()
    with LOG.open("a", newline="") as file:
        writer = csv.writer(file)
        if new_log:
            writer.writerow(["date", "download", "normalization", "details"])

        month = start.replace(day=1)
        downloaded = normalized = skipped = failed = 0

        while month <= end:
            first = max(start, month)
            last = min(end, month_end(month))

            print(f"\nPROCESSING {month:%Y-%m}")

            subprocess.run([
                sys.executable, DOWNLOADER,
                "--start", first.isoformat(),
                "--end", last.isoformat()
            ])

            day = first
            while day <= last:
                if day.weekday() < 5:
                    key = day.strftime("%Y%m%d")
                    archive = ARCHIVES / f"nse_bhavcopy_{key}.zip"
                    output = SILVER / f"nse_equity_prices_{key}.parquet"

                    if output.exists():
                        skipped += 1
                        writer.writerow([key, "PRESENT", "PRESENT", "Skipped"])
                    elif not archive.exists():
                        failed += 1
                        writer.writerow([key, "MISSING", "NOT_RUN", "Archive unavailable"])
                    else:
                        result = subprocess.run([
                            sys.executable, NORMALIZER, str(archive)
                        ], capture_output=True, text=True)

                        if result.returncode == 0 and output.exists():
                            normalized += 1
                            downloaded += 1
                            writer.writerow([key, "PRESENT", "SUCCESS", ""])
                            print("NORMALIZED", key)
                        else:
                            failed += 1
                            detail = (result.stderr or result.stdout).strip()
                            writer.writerow([key, "PRESENT", "FAILED", detail[:500]])
                            print("FAILED", key, detail[-500:])

                    file.flush()

                day += timedelta(days=1)

            month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)

    print("\nBACKFILL SUMMARY")
    print("Newly normalized:", normalized)
    print("Already normalized:", skipped)
    print("Missing or failed:", failed)
    print("Log:", LOG)

if __name__ == "__main__":
    main()

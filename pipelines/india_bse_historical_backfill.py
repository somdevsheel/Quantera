import argparse
import calendar
import csv
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "data/silver/reference/india/bse_daily_api_candidates.csv"
DOWNLOADER = ROOT / "pipelines/india_bse_historical_downloader.py"
NORMALIZER = ROOT / "pipelines/india_bse_historical_normalizer.py"
LOG = ROOT / "data/staging/india/bse_historical_backfill_log.csv"


def months_between(start, end):
    current = date(start.year, start.month, 1)
    final = date(end.year, end.month, 1)

    while current <= final:
        yield current.year, current.month
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)


def main():
    parser = argparse.ArgumentParser(
        description="Resumable monthly BSE historical backfill"
    )
    parser.add_argument("--start", required=True, help="YYYY-MM")
    parser.add_argument("--end", required=True, help="YYYY-MM")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--codes", help="Optional comma-separated BSE codes")
    parser.add_argument("--pause", type=float, default=0.6)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()

    start = date.fromisoformat(args.start + "-01")
    end = date.fromisoformat(args.end + "-01")

    if start > end:
        parser.error("--start must be before or equal to --end")

    if args.codes:
        codes = [x.strip() for x in args.codes.split(",") if x.strip()]
    else:
        candidates = pd.read_csv(CANDIDATES, dtype=str)
        codes = candidates["bse_scrip_code"].dropna().tolist()
        codes = codes[:args.limit]

    if not codes:
        raise SystemExit("No BSE securities selected.")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    log_exists = LOG.exists()

    with LOG.open("a", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "year", "month", "codes",
                "download_exit_code", "normalizer_exit_code", "status"
            ],
        )
        if not log_exists:
            writer.writeheader()

        print(f"Selected securities: {len(codes)}")
        print("BSE codes:", ", ".join(codes))

        for year, month in months_between(start, end):
            first_day = f"{year:04d}-{month:02d}-01"
            last_day = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]}"

            print(f"\n{'=' * 55}")
            print(f"PROCESSING {year}-{month:02d}")
            print(f"{'=' * 55}")

            download = subprocess.run(
                [
                    sys.executable, str(DOWNLOADER),
                    "--codes", ",".join(codes),
                    "--start", first_day,
                    "--end", last_day,
                    "--pause", str(args.pause),
                    "--retries", str(args.retries),
                ],
                cwd=ROOT,
            )

            normalize = subprocess.run(
                [
                    sys.executable, str(NORMALIZER),
                    "--start", f"{year:04d}-{month:02d}",
                    "--end", f"{year:04d}-{month:02d}",
                    "--codes", ",".join(codes),
                ],
                cwd=ROOT,
            )

            status = (
                "SUCCESS"
                if download.returncode == 0 and normalize.returncode == 0
                else "FAILED"
            )

            writer.writerow({
                "year": year,
                "month": f"{month:02d}",
                "codes": ",".join(codes),
                "download_exit_code": download.returncode,
                "normalizer_exit_code": normalize.returncode,
                "status": status,
            })
            f.flush()

            print(f"MONTH STATUS: {status}")

    print("\nBSE BACKFILL FINISHED")
    print("Securities:", len(codes))
    print("Progress log:", LOG)


if __name__ == "__main__":
    main()

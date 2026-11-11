import argparse
import calendar
import csv
import json
import os
import time
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]

BASE_URL = (
    "https://api.bseindia.com/"
    "BseIndiaAPI/api/StockpricesearchData/w"
)

BRONZE = ROOT / "data/bronze/india/equity_prices/bse"
LOG = ROOT / "data/staging/india/bse_historical_download_log.csv"

FIELDS = [
    "scrip_code", "year", "month", "status",
    "record_count", "http_status", "file", "details"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Origin": "https://www.bseindia.com",
    "Referer": "https://www.bseindia.com/",
    "Accept": "application/json, text/plain, */*",
}


def write_log(**kwargs):
    new_file = not LOG.exists()

    with LOG.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)

        if new_file:
            writer.writeheader()

        writer.writerow(kwargs)
        f.flush()
        os.fsync(f.fileno())


def validate_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")

    if "StockData" not in payload:
        raise ValueError("Missing StockData field")

    rows = payload["StockData"]

    # BSE returns null when no historical records exist.
    if rows is None:
        return []

    if not isinstance(rows, list):
        raise ValueError("StockData must be a list or null")

    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("StockData contains a non-object record")

    return rows


def read_existing(target):
    if not target.exists():
        return None

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        rows = validate_payload(payload)
        return len(rows)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def next_month(current):
    if current.month == 12:
        return date(current.year + 1, 1, 1)
    return date(current.year, current.month + 1, 1)


def main():
    parser = argparse.ArgumentParser(
        description="Resumable BSE daily historical price downloader"
    )

    parser.add_argument("--codes", required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--pause", type=float, default=0.6)
    parser.add_argument("--retries", type=int, default=3)

    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    if end < start:
        parser.error("--end must not precede --start")

    if start.day != 1:
        parser.error("--start must be the first day of a month")

    if end.day != calendar.monthrange(end.year, end.month)[1]:
        parser.error("--end must be the last day of a month")

    if args.pause < 0 or args.retries < 1:
        parser.error("--pause must be non-negative and --retries at least 1")

    codes = list(dict.fromkeys(
        x.strip() for x in args.codes.split(",") if x.strip()
    ))

    if not codes:
        parser.error("At least one BSE scrip code is required")

    BRONZE.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)

    counts = {
        "downloaded": 0,
        "empty": 0,
        "existing": 0,
        "failed": 0,
    }

    session = requests.Session()
    session.headers.update(HEADERS)

    print("BSE HISTORICAL DOWNLOADER")
    print("Securities:", len(codes))
    print("Period:", start, "to", end)
    print("Pause:", args.pause, "seconds")
    print("Retries:", args.retries)

    for code in codes:
        if not code.isdigit():
            print(f"INVALID CODE: {code}")
            counts["failed"] += 1
            continue

        month = date(start.year, start.month, 1)

        while month <= end:
            year_month = month.strftime("%Y%m")
            year = month.year
            month_num = month.month

            target_dir = (
                BRONZE
                / f"year={year}"
                / f"month={month_num:02d}"
            )
            target_dir.mkdir(parents=True, exist_ok=True)

            target = target_dir / f"bse_daily_{code}_{year_month}.json"

            existing_count = read_existing(target)

            if existing_count is not None:
                counts["existing"] += 1

                write_log(
                    scrip_code=code,
                    year=year,
                    month=month_num,
                    status="EXISTING",
                    record_count=existing_count,
                    http_status="",
                    file=str(target.relative_to(ROOT)),
                    details="Validated existing Bronze JSON",
                )

                print(f"EXISTS {code} {year_month}: {existing_count}")
                month = next_month(month)
                continue

            batch_start = month
            batch_end = date(
                year,
                month_num,
                calendar.monthrange(year, month_num)[1],
            )

            params = {
                "MonthDate": batch_start.strftime("%d/%m/%Y"),
                "YearDate": batch_end.strftime("%d/%m/%Y"),
                "pageType": "0",
                "Scode": code,
                "Seg": "C",
                "rbType": "D",
                "SortOrder": "true",
            }

            completed = False
            last_error = ""
            last_http_status = ""

            for attempt in range(1, args.retries + 1):
                try:
                    response = session.get(
                        BASE_URL,
                        params=params,
                        timeout=(10, 45),
                    )
                    last_http_status = response.status_code

                    if response.status_code == 403:
                        raise requests.HTTPError(
                            "HTTP 403: BSE access forbidden; not retrying",
                            response=response,
                        )

                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < args.retries:
                            delay = min(60, 3 * (2 ** (attempt - 1)))
                            print(
                                f"RETRY {code} {year_month}: "
                                f"HTTP {response.status_code}, wait {delay}s"
                            )
                            time.sleep(delay)
                            continue

                    response.raise_for_status()

                    payload = response.json()
                    rows = validate_payload(payload)

                    temporary = target.with_suffix(".json.tmp")
                    temporary.write_bytes(response.content)
                    os.replace(temporary, target)

                    status = "DOWNLOADED" if rows else "EMPTY"
                    counts["downloaded" if rows else "empty"] += 1

                    write_log(
                        scrip_code=code,
                        year=year,
                        month=month_num,
                        status=status,
                        record_count=len(rows),
                        http_status=response.status_code,
                        file=str(target.relative_to(ROOT)),
                        details=f"HTTP request attempt {attempt}",
                    )

                    print(
                        f"{status} {code} {year_month}: "
                        f"{len(rows)} records"
                    )

                    completed = True
                    break

                except (requests.RequestException, ValueError) as exc:
                    last_error = str(exc)[:500]

                    if isinstance(exc, requests.HTTPError):
                        if exc.response is not None:
                            last_http_status = exc.response.status_code

                        if last_http_status == 403:
                            break

                        if last_http_status and 400 <= int(last_http_status) < 500:
                            break

                    if attempt < args.retries:
                        delay = min(60, 3 * (2 ** (attempt - 1)))
                        print(
                            f"RETRY {code} {year_month}: "
                            f"{last_error}; wait {delay}s"
                        )
                        time.sleep(delay)

            if not completed:
                counts["failed"] += 1

                write_log(
                    scrip_code=code,
                    year=year,
                    month=month_num,
                    status="FAILED",
                    record_count=0,
                    http_status=last_http_status,
                    file="",
                    details=last_error or "Download failed",
                )

                print(
                    f"FAILED {code} {year_month}: "
                    f"{last_error}"
                )

            time.sleep(args.pause)
            month = next_month(month)

    session.close()

    print("\nBSE DOWNLOAD SUMMARY")
    for key, value in counts.items():
        print(f"{key.title()}: {value}")

    print("Bronze:", BRONZE)
    print("Log:", LOG)


if __name__ == "__main__":
    main()

from pathlib import Path
from datetime import datetime, timezone
import json

import polars as pl


DATA_DIR = Path("data/staging/market")
REPORT_DIR = Path("data/validation/reports")

REQUIRED_COLUMNS = [
    "symbol",
    "trading_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
]

PRICE_COLUMNS = ["open", "high", "low", "close"]


def validate_file(file_path):
    df = pl.read_parquet(file_path)
    issues = []
    checks = {}

    # 1. Schema validation
    missing_columns = [
        column for column in REQUIRED_COLUMNS
        if column not in df.columns
    ]

    checks["schema_valid"] = len(missing_columns) == 0

    if missing_columns:
        issues.append({
            "type": "MISSING_COLUMNS",
            "details": missing_columns,
        })
        return {
            "file": file_path.name,
            "status": "FAILED",
            "records": df.height,
            "checks": checks,
            "issues": issues,
        }

    # 2. Null value validation
    null_counts = {}

    for column in REQUIRED_COLUMNS:
        count = df[column].null_count()
        null_counts[column] = count

        if count > 0:
            issues.append({
                "type": "NULL_VALUES",
                "column": column,
                "count": count,
            })

    checks["null_validation"] = all(
        count == 0 for count in null_counts.values()
    )

    # 3. Duplicate symbol/date validation
    duplicate_count = (
        df.group_by(["symbol", "trading_date"])
        .len()
        .filter(pl.col("len") > 1)
        .height
    )

    checks["duplicate_validation"] = duplicate_count == 0

    if duplicate_count:
        issues.append({
            "type": "DUPLICATE_RECORDS",
            "count": duplicate_count,
        })

    # 4. Negative or zero prices
    invalid_prices = df.filter(
        pl.any_horizontal([
            pl.col(column) <= 0
            for column in PRICE_COLUMNS
        ])
    ).height

    checks["positive_price_validation"] = invalid_prices == 0

    if invalid_prices:
        issues.append({
            "type": "INVALID_PRICES",
            "count": invalid_prices,
        })

    # 5. OHLC relationship validation
    invalid_ohlc = df.filter(
        (pl.col("high") < pl.col("low"))
        | (pl.col("high") < pl.max_horizontal("open", "close"))
        | (pl.col("low") > pl.min_horizontal("open", "close"))
    ).height

    checks["ohlc_validation"] = invalid_ohlc == 0

    if invalid_ohlc:
        issues.append({
            "type": "INVALID_OHLC",
            "count": invalid_ohlc,
        })

    # 6. Negative volume
    invalid_volume = df.filter(
        pl.col("volume") < 0
    ).height

    checks["volume_validation"] = invalid_volume == 0

    if invalid_volume:
        issues.append({
            "type": "NEGATIVE_VOLUME",
            "count": invalid_volume,
        })

    # 7. Extreme daily price movements
    movement_df = df.sort("trading_date").with_columns(
        (
            pl.col("close") /
            pl.col("close").shift(1) - 1
        ).alias("daily_return")
    )

    extreme_moves = movement_df.filter(
        pl.col("daily_return").abs() > 0.50
    )

    extreme_count = extreme_moves.height

    if extreme_count:
        issues.append({
            "type": "EXTREME_DAILY_MOVEMENTS",
            "count": extreme_count,
            "threshold": "Absolute daily return greater than 50%",
            "note": "Requires review; not automatically considered invalid.",
        })

    checks["extreme_movement_review"] = extreme_count == 0

    # 8. Date range
    date_range = {
        "start": str(df["trading_date"].min()),
        "end": str(df["trading_date"].max()),
    }

    # Final status
    failed_checks = [
        name for name, passed in checks.items()
        if not passed and name != "extreme_movement_review"
    ]

    status = "PASSED" if not failed_checks else "FAILED"

    return {
        "file": file_path.name,
        "symbol": str(df["symbol"][0]),
        "status": status,
        "records": df.height,
        "date_range": date_range,
        "checks": checks,
        "null_counts": null_counts,
        "issues": issues,
    }


def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    files = sorted(DATA_DIR.glob("*.parquet"))

    if not files:
        print("No Parquet files found.")
        return

    results = []

    for file_path in files:
        try:
            result = validate_file(file_path)
            results.append(result)

            print(
                f"{result['symbol'] if 'symbol' in result else file_path.stem}: "
                f"{result['status']} | "
                f"{result['records']} records | "
                f"{len(result['issues'])} issues"
            )

        except Exception as error:
            results.append({
                "file": file_path.name,
                "status": "ERROR",
                "error": str(error),
            })
            print(f"ERROR: {file_path.name}: {error}")

    passed = sum(
        result["status"] == "PASSED" for result in results
    )

    failed = sum(
        result["status"] == "FAILED" for result in results
    )

    report = {
        "platform": "Quantera",
        "validation_timestamp": datetime.now(
            timezone.utc
        ).isoformat(),
        "files_scanned": len(files),
        "passed": passed,
        "failed": failed,
        "results": results,
    }

    report_path = REPORT_DIR / "market_data_validation.json"

    report_path.write_text(
        json.dumps(report, indent=4, default=str)
    )

    print("\nValidation Summary")
    print("------------------")
    print(f"Files scanned: {len(files)}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()

"""Upload / verify India data-lake files in S3 from the audit manifest.

Reads data/validation/india/file_manifest.csv (run validation/india_lake_audit.py first).
Key convention matches existing objects: local path minus the leading "data/".
NSE equity raw archives kept under data/staging/india/historical/ are mapped
to bronze/india/equity_prices/nse/archives/.

Each object carries x-amz-meta-sha256; an object is "VERIFIED" when it exists
with the same size and sha256 metadata. Encryption: SSE-S3 (AES256).

  python scripts/sync_india_to_s3.py                 # dry run: report what would upload
  python scripts/sync_india_to_s3.py --execute       # upload missing/changed objects
  python scripts/sync_india_to_s3.py --layer bronze  # restrict to one layer
Log: data/validation/india/s3_sync_log.csv
"""
import argparse
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import boto3
import pandas as pd
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/validation/india/file_manifest.csv"
LOG = ROOT / "data/validation/india/s3_sync_log.csv"
BUCKET = os.environ.get("S3_BUCKET", "quant-global-data-lake-prod")
REGION = os.environ.get("AWS_REGION", "ap-southeast-2")


def s3_key(path):
    if path.startswith("data/staging/india/historical/"):
        return "bronze/india/equity_prices/nse/archives/" + path.split("/historical/", 1)[1]
    return path.removeprefix("data/")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true")
    p.add_argument("--layer", choices=["bronze", "silver", "gold"])
    p.add_argument("--dataset")
    p.add_argument("--workers", type=int, default=8)
    a = p.parse_args()

    m = pd.read_csv(MANIFEST)
    if a.layer:
        m = m[m.layer == a.layer]
    if a.dataset:
        m = m[m.dataset == a.dataset]
    if m.sha256.isna().any():
        raise SystemExit("Manifest has files without sha256; rerun the audit with checksums.")

    s3 = boto3.client("s3", region_name=REGION)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def handle(row):
        key = s3_key(row.path)
        try:
            head = s3.head_object(Bucket=BUCKET, Key=key)
            if head["ContentLength"] == row.bytes and head.get("Metadata", {}).get("sha256") == row.sha256:
                return key, "VERIFIED", ""
            state = "CHANGED"
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("404", "NoSuchKey", "NotFound"):
                return key, "ERROR", str(exc)[:200]
            state = "MISSING"
        if not a.execute:
            return key, f"WOULD_UPLOAD_{state}", ""
        try:
            s3.upload_file(str(ROOT / row.path), BUCKET, key, ExtraArgs={
                "ServerSideEncryption": "AES256",
                "Metadata": {"sha256": row.sha256, "dataset": row.dataset, "layer": row.layer}})
            return key, f"UPLOADED_{state}", ""
        except Exception as exc:  # noqa: BLE001
            return key, "ERROR", str(exc)[:200]

    with ThreadPoolExecutor(a.workers) as pool:
        results = list(pool.map(handle, m.itertuples()))
    out = m.assign(s3_bucket=BUCKET, s3_key=[r[0] for r in results],
                   s3_status=[r[1] for r in results], s3_error=[r[2] for r in results], checked_utc=now)
    out.to_csv(LOG, mode="a", header=not LOG.exists(), index=False)
    print(out.groupby(["layer", "s3_status"]).size().to_string())
    print("Bucket:", BUCKET, "| mode:", "EXECUTE" if a.execute else "DRY RUN")


if __name__ == "__main__":
    main()

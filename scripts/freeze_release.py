"""Freeze a lake release: an immutable, checksummed snapshot record of data/.

A release is a manifest, not a copy: every Bronze/Silver/Gold file with bytes and sha256,
plus the audit results it passed. Later, --verify reports any file that changed, vanished
or appeared since the release, so consumers (API, Quantera) can pin a release.

  python scripts/freeze_release.py --tag lake-2026.11            # refuses if jobs run / audit fails
  python scripts/freeze_release.py --verify lake-2026.11

Output: data/releases/<tag>/release_manifest.parquet, release.json
Preconditions (override only with --force, recorded in release.json):
  * no ingestion job running
  * India audit has 0 FAIL  (data/validation/india/audit_summary.json)
  * SEC balance-sheet reconciliation report exists
Git tagging is left to the user (the data itself is not in git).
"""
import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REL = ROOT / "data/releases"
LAYERS = ["bronze", "silver", "gold", "quarantine", "registry", "reference"]
INDIA_MANIFEST = ROOT / "data/validation/india/file_manifest.csv"
JOBS = r"^python .*(india_nse_fo_backfill|india_nse_financial_results|download_xbrl_filings|india_nse_option_greeks|global_etf_nport)\.py"


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan():
    cache = {}
    if INDIA_MANIFEST.exists():
        m = pd.read_csv(INDIA_MANIFEST)
        cache = {(r.path, r.bytes, r.mtime): r.sha256 for r in m.itertuples() if isinstance(r.sha256, str)}
    rows = []
    for layer in LAYERS:
        base = ROOT / "data" / layer
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.name.endswith(".tmp"):
                continue
            st = p.stat()
            rel = str(p.relative_to(ROOT))
            digest = cache.get((rel, st.st_size, int(st.st_mtime))) or sha256(p)
            rows.append({"layer": layer, "path": rel, "bytes": st.st_size, "mtime": int(st.st_mtime), "sha256": digest})
    return pd.DataFrame(rows)


def checks():
    problems = []
    if subprocess.run(["pgrep", "-f", JOBS], capture_output=True).returncode == 0:
        problems.append("ingestion jobs still running")
    s = ROOT / "data/validation/india/audit_summary.json"
    audit = json.loads(s.read_text()) if s.exists() else {}
    if not audit:
        problems.append("India audit missing (run validation/india_lake_audit.py)")
    elif audit.get("tests", {}).get("FAIL", 0):
        problems.append(f"India audit has {audit['tests']['FAIL']} FAIL")
    if not (ROOT / "data/validation/fundamentals/sec_balance_sheet_reconciliation.csv").exists():
        problems.append("SEC reconciliation report missing")
    return problems, audit


def freeze(tag, force, note):
    out = REL / tag
    if out.exists():
        raise SystemExit(f"release {tag} already exists; releases are immutable")
    problems, audit = checks()
    if problems and not force:
        raise SystemExit("refusing to freeze: " + "; ".join(problems))
    m = scan()
    out.mkdir(parents=True)
    m.to_parquet(out / "release_manifest.parquet", index=False)
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
    meta = {
        "tag": tag, "frozen_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": len(m), "bytes": int(m.bytes.sum()),
        "by_layer": m.groupby("layer").agg(files=("path", "count"), bytes=("bytes", "sum")).to_dict("index"),
        "code_git_head": git, "code_git_dirty": dirty,
        "india_audit": audit.get("tests"), "forced": bool(problems), "problems_at_freeze": problems,
        "note": note,
    }
    (out / "release.json").write_text(json.dumps(meta, indent=2, default=int))
    print(json.dumps({k: meta[k] for k in ("tag", "files", "bytes", "india_audit", "forced", "problems_at_freeze")}, indent=2))


def verify(tag):
    rel = pd.read_parquet(REL / tag / "release_manifest.parquet")
    now = scan()
    j = rel.merge(now, on="path", how="outer", suffixes=("_rel", "_now"), indicator=True)
    missing = j[j._merge == "left_only"]
    added = j[j._merge == "right_only"]
    changed = j[(j._merge == "both") & (j.sha256_rel != j.sha256_now)]
    print(f"release {tag}: {len(rel)} files | missing {len(missing)} | changed {len(changed)} | added since {len(added)}")
    for name, d in (("MISSING", missing), ("CHANGED", changed)):
        for p in d.path.head(20):
            print(f"  {name} {p}")
    return 1 if len(missing) or len(changed) else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag")
    ap.add_argument("--verify")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    if a.verify:
        raise SystemExit(verify(a.verify))
    if not a.tag:
        ap.error("--tag or --verify required")
    freeze(a.tag, a.force, a.note)


if __name__ == "__main__":
    main()

"""Per-release "hot" cache for long single-instrument price histories (docs/api_design.md §6).

Daily equity files are one-file-per-day (~5,000-6,400 files), so a 20-year history for
one stock opens every file. The cache re-sorts the *same release files* into one Parquet
file per exchange, ordered by instrument key + date with small row groups, so a lookup
reads a few row groups.

  python -m api.cache --build [--tag lake-2026.11]      # writes data/releases/<tag>/api_cache/
  python -m api.cache --check [--tag lake-2026.11]

The cache is derived and disposable: it is built only from files listed in the release
manifest, and the API uses it only if its recorded source-file list hash matches the release.
Otherwise the API falls back to the per-day files (correct, just slower).
"""
import argparse
import hashlib
import json
import time
from datetime import datetime, timezone

import duckdb
import pyarrow.parquet as pq

from . import datasets as ds
from .release import REL_DIR, Release

VERSION = "api_cache_v2"  # v2: NSE keyed by security_key (identity history)
ROW_GROUP = 32_768

# NSE rows keyed by security (gold/india/reference/nse_security_id_history): every ISIN of a
# security and its pre-2011 symbol rows share one key. Used by the cache build and by the
# API's uncached path, so both select exactly the same rows.
NSE_KEYED = """
    SELECT coalesce(si.security_key, ss.security_key, p.instrument_id, 'NSESYM:' || p.symbol) AS lookup_key, p.*
    FROM read_parquet({files}, union_by_name=true) AS p
    LEFT JOIN (SELECT DISTINCT instrument_id, security_key FROM read_parquet({hist})
               WHERE instrument_id IS NOT NULL) AS si ON p.instrument_id = si.instrument_id
    LEFT JOIN (SELECT symbol, first_date, last_date, security_key FROM read_parquet({hist})
               WHERE instrument_id IS NULL) AS ss
           ON p.instrument_id IS NULL AND p.symbol = ss.symbol
          AND CAST(p.trade_date AS DATE) BETWEEN ss.first_date AND ss.last_date"""

# lookup_key: what an API query filters on; rows are sorted by it.
SPECS = {
    "nse_equity_daily": NSE_KEYED.format(files="$files", hist="$hist"),
    "bse_equity_daily": "SELECT bse_scrip_code AS lookup_key, * FROM read_parquet($files, union_by_name=true)",
}
EXTRA_SOURCES = {"nse_equity_daily": "nse_security_id_history"}


def _source_hash(files):
    h = hashlib.sha256()
    for f in files:
        h.update(f.encode())
        h.update(b"\n")
    return h.hexdigest()


def cache_dir(tag):
    return REL_DIR / tag / "api_cache"


def build(tag=None):
    rel = Release(tag)
    dsets = ds.load(rel.paths)
    out = cache_dir(rel.tag)
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(config={"threads": 8, "memory_limit": "12GB"})
    meta = {"version": VERSION, "release": rel.tag, "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "datasets": {}}
    for name, select in SPECS.items():
        files = [p for _, p in dsets[name].files]
        params = {"files": files}
        if name in EXTRA_SOURCES:
            params["hist"] = [p for _, p in dsets[EXTRA_SOURCES[name]].files]
            files = files + params["hist"]
        target, tmp = out / f"{name}.parquet", out / f"{name}.parquet.tmp"
        t = time.time()
        con.execute(f"""COPY ({select} ORDER BY lookup_key, trade_date)
                        TO '{tmp}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {ROW_GROUP})""", params)
        src_rows = con.execute("SELECT count(*) FROM read_parquet($files, union_by_name=true)",
                               {"files": params["files"]}).fetchall()[0][0]
        rows = pq.ParquetFile(tmp).metadata.num_rows
        if rows != src_rows:
            tmp.unlink()
            raise SystemExit(f"{name}: cache rows {rows} != source rows {src_rows}")
        tmp.replace(target)
        meta["datasets"][name] = {"path": target.name, "rows": rows, "bytes": target.stat().st_size,
                                  "source_files": len(files), "source_list_sha256": _source_hash(files)}
        print(f"{name}: {rows:,} rows from {len(files):,} files -> {target.stat().st_size / 1e6:.0f} MB "
              f"in {time.time() - t:.0f}s")
    (out / "cache_manifest.json").write_text(json.dumps(meta, indent=2))
    return meta


def load(release, dsets):
    """{dataset name: cache file path} for cache entries valid for this release."""
    f = cache_dir(release.tag) / "cache_manifest.json"
    if not f.exists():
        return {}, "no api_cache for this release (python -m api.cache --build)"
    meta = json.loads(f.read_text())
    valid, problems = {}, []
    for name, m in meta.get("datasets", {}).items():
        path = f.parent / m["path"]
        files = [p for _, p in dsets[name].files] if name in dsets else []
        if name in EXTRA_SOURCES:
            files += [p for _, p in dsets[EXTRA_SOURCES[name]].files]
        if meta.get("version") != VERSION or meta.get("release") != release.tag:
            problems.append(f"{name}: built for {meta.get('release')}/{meta.get('version')}")
        elif not path.exists() or path.stat().st_size != m["bytes"]:
            problems.append(f"{name}: cache file missing or resized")
        elif _source_hash(files) != m["source_list_sha256"]:
            problems.append(f"{name}: source file list differs from release")
        else:
            valid[name] = str(path)
    return valid, "; ".join(problems) or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--build", action="store_true")
    g.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.build:
        build(a.tag)
    rel = Release(a.tag)
    valid, problem = load(rel, ds.load(rel.paths))
    print("valid cache entries:", sorted(valid), "| problems:", problem)


if __name__ == "__main__":
    main()

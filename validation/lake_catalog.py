"""Lake-wide catalog: every dataset in Bronze / Silver / Gold with files, bytes,
rows, schema, date range and partition layout.

A dataset = the directory path under data/<layer>/ with partition folders
(key=value) and file names collapsed, e.g.
  silver/india/equity_prices/bse_bhavcopy/{year}/{month}/bse_bhavcopy_{YYYYMMDD}.parquet

Outputs: data/validation/catalog/lake_catalog.csv, lake_catalog.md,
         lake_schemas.csv (one row per dataset x column, full types)
Update cadence / refresh command / usage rights come from reference/dataset_cadence.csv
(longest matching path prefix).
Row counts and schema come from Parquet footers (no full scans); the date range
is computed with DuckDB on the first recognised date column.
"""
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/validation/catalog"
LAYERS = ["bronze", "silver", "gold"]
DATE_COLS = ["trade_date", "trading_date", "date", "observation_date", "nav_date", "ex_date",
             "event_date", "to_date", "period_end", "filing_date"]
SKIP = (".tmp", ".bak")


ENTITY_DIR_THRESHOLD = 25  # a folder with more child folders than this = one folder per entity


def dataset_key(rel, many_children):
    """Dataset = folder path with key=value partitions -> {key} and per-entity
    folders -> {entity}; files are grouped by extension (per-day / per-symbol
    file names are not part of the dataset identity)."""
    parts = rel.parts
    dirs, cur = [], ROOT / "data"
    for p in parts[:-1]:
        if "=" in p:
            dirs.append("{" + p.split("=")[0] + "}")
        elif cur in many_children:
            dirs.append("{entity}")
        elif re.fullmatch(r"\d{10}-\d{2}-\d{6}", p):
            dirs.append("{accession}")
        else:
            dirs.append(p)
        cur = cur / p
    return "/".join(dirs), "*" + (Path(parts[-1]).suffix.lower() or "")


def main():
    groups = defaultdict(list)
    for layer in LAYERS:
        base = ROOT / "data" / layer
        if not base.exists():
            continue
        all_paths = list(base.rglob("*"))
        child_dirs = defaultdict(int)
        for d in all_paths:
            if d.is_dir() and "=" not in d.name:
                child_dirs[d.parent] += 1
        many = {p for p, n in child_dirs.items() if n > ENTITY_DIR_THRESHOLD}
        for f in all_paths:
            if f.is_file() and not f.name.endswith(SKIP):
                rel = f.relative_to(ROOT / "data")
                groups[dataset_key(rel, many)].append(f)

    import pandas as pd
    cad = pd.read_csv(ROOT / "reference/dataset_cadence.csv")
    cad = cad.assign(n=cad.path_prefix.str.len()).sort_values("n", ascending=False)

    def cadence(path):
        for r in cad.itertuples():
            if path.startswith(r.path_prefix):
                return r.update_cadence, r.refresh_command, r.usage_rights
        return None, None, None

    con = duckdb.connect()
    rows, schema_rows = [], []
    for (folder, pattern), files in sorted(groups.items()):
        size = sum(f.stat().st_size for f in files)
        rec = {"layer": folder.split("/")[0], "dataset_path": folder, "file_pattern": pattern,
               "files": len(files), "bytes": size,
               "partitions": ",".join(sorted(set(re.findall(r"\{(\w+)\}", folder)))) or None,
               "rows": None, "columns": None, "schema": None, "first_date": None, "last_date": None,
               "schema_version": None, "has_lineage": None}
        rec["update_cadence"], rec["refresh_command"], rec["usage_rights"] = cadence(folder)
        parquet = [f for f in files if f.suffix == ".parquet"]
        if parquet:
            try:
                rec["rows"] = sum(pq.ParquetFile(f).metadata.num_rows for f in parquet)
                schema = pq.read_schema(parquet[0])
                rec["columns"] = len(schema.names)
                rec["schema"] = "; ".join(f"{n}:{t}" for n, t in zip(schema.names, schema.types))[:2000]
                schema_rows += [{"dataset_path": folder, "file_pattern": pattern, "position": i,
                                 "column": n, "type": str(t)} for i, (n, t) in enumerate(zip(schema.names, schema.types))]
                rec["has_lineage"] = any(c in schema.names for c in ("source_file", "accession", "source_url", "observation_id"))
                if "schema_version" in schema.names:
                    sv = pq.read_table(parquet[-1], columns=["schema_version"]).column(0).unique().to_pylist()
                    rec["schema_version"] = ",".join(str(v) for v in sv if v is not None)
                dcol = next((c for c in DATE_COLS if c in schema.names), None)
                if dcol:
                    glob = [str(f) for f in parquet]
                    lo, hi = con.execute(
                        f"SELECT min(TRY_CAST({dcol} AS DATE)), max(TRY_CAST({dcol} AS DATE)) "
                        f"FROM read_parquet(?, union_by_name=true)", [glob]).fetchone()
                    rec.update(date_column=dcol, first_date=str(lo), last_date=str(hi))
            except Exception as exc:  # noqa: BLE001
                rec["schema"] = f"ERROR: {exc}"[:300]
        rows.append(rec)

    pd.DataFrame(schema_rows).to_csv(OUT / "lake_schemas.csv", index=False)
    cat = pd.DataFrame(rows).sort_values(["layer", "dataset_path"])
    OUT.mkdir(parents=True, exist_ok=True)
    cat.to_csv(OUT / "lake_catalog.csv", index=False)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [f"# Data lake catalog\n\nGenerated {stamp}. {len(cat)} datasets, "
             f"{int(cat.files.sum()):,} files, {cat.bytes.sum() / 1e9:.2f} GB.\n",
             "| layer | dataset | pattern | files | GB | rows | dates | schema | cadence |", "|---|---|---|---|---|---|---|---|---|"]
    for r in cat.itertuples():
        dates = f"{r.first_date} → {r.last_date}" if isinstance(r.first_date, str) else ""
        rows_s = f"{int(r.rows):,}" if r.rows == r.rows and r.rows is not None else ""
        lines.append(f"| {r.layer} | `{r.dataset_path}` | `{r.file_pattern}` | {r.files:,} | "
                     f"{r.bytes / 1e9:.3f} | {rows_s} | {dates} | {r.schema_version or ''} | {r.update_cadence or ''} |")
    (OUT / "lake_catalog.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:3]))
    print(cat.groupby("layer")[["files", "bytes"]].sum().assign(GB=lambda d: (d.bytes / 1e9).round(2)).to_string())


if __name__ == "__main__":
    main()

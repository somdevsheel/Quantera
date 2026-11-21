# Quantera Data Lake

A local, point-in-time financial data lake for Quantera, and a read-only API to query it.

| Area | What is in it |
|---|---|
| India | NSE and BSE daily equities (2000 →), NSE F&O (2000 →) with implied vol and Greeks, NSE indices and India VIX, AMFI mutual-fund NAV, corporate actions, trading and event calendars, NSE financial results (XBRL) |
| US (SEC) | Filing-level XBRL facts, point-in-time statements with balance-sheet reconciliation, ETF holdings (N-PORT) |
| Global | FRED macro, rates, FX, commodities and volatility; Yahoo equities (research use only) |

Data is stored as Parquet in Bronze (raw, immutable), Silver (normalized) and Gold (derived) layers under `data/`. A registry of instruments lives in SQLite.

> **The data is not in git.** `data/` (about 40 GB) is git-ignored. On a new machine, either copy `data/` from the local backup or rebuild it with the pipelines (the full backfill takes days).

---

## 1. Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

- Run every command from the repository root with the venv active.
- Storage is **local only**; there is no S3. Keep a second copy on another disk (see §5).
- Secrets go in `.env`, which is git-ignored. Never commit AWS keys.

## 2. Repository layout

```
api/                 Read-only data API (FastAPI + DuckDB), pinned to a frozen release
  adapters/          One module per dataset family (prices, derivatives, funds, fundamentals, macro, reference)
pipelines/           India and global ingestion: download → Bronze → Silver → Gold
ingestion/providers/ Market data and SEC fundamentals (XBRL download, normalizer, statement builder)
normalization/       Silver builders and the corporate-action engine (market / FX)
registry/            Instrument and exchange registry (data/registry/quantera_registry.db)
analytics/           Returns, risk, indicators, portfolio tools on Gold data
validation/          India audit (validation/india_lake_audit.py) and the lake-wide catalog
scripts/             Finalize, backup, release freeze, API load test
reference/           Universes, FRED series lists, dataset cadence and usage rights
tests/api/           API validation suite
docs/                india_data_lake.md (India datasets and methods), api_design.md (API)
```

## 3. Updating the data

All pipelines can be restarted. Rerunning a pipeline skips what is already done.

```bash
# India: daily, after about 18:00 IST
python pipelines/india_nse_udiff_backfill.py    --start 2026-11-09 --end 2026-11-12
python pipelines/india_bse_bhavcopy_backfill.py --start 2026-11-09 --end 2026-11-12
python pipelines/india_nse_indices_backfill.py  --start 2026-11-09 --end 2026-11-12
python pipelines/india_nse_fo_backfill.py       --start 2026-11-09 --end 2026-11-12
python pipelines/india_amfi_nav_backfill.py     --start 2026-11 --end 2026-11

# India: weekly / monthly
python pipelines/india_nse_corporate_actions.py --start 2026-11
python pipelines/india_nse_reference_lists.py    # symbol changes, index constituents + industry (snapshot)
python pipelines/india_nse_event_calendar.py   --start 2026-11
python pipelines/india_fred_series_backfill.py

# India: quarterly (results season)
python pipelines/india_nse_financial_results.py --stage all

# SEC (quarterly) and global
python ingestion/providers/fundamentals/sec_filings/download_xbrl_filings.py --stage all
python pipelines/global_etf_nport.py --stage all
python pipelines/india_fred_series_backfill.py --catalog reference/series/global_fred_series.csv --region global
```

Then rebuild everything derived, run the audits and refresh the catalog in one step:

```bash
bash scripts/finalize_lake.sh
```

This covers option Greeks, the trading calendar, adjustment factors, SEC statements, ETF holdings, the India audit and `data/validation/catalog/`.

`reference/dataset_cadence.csv` lists the update cadence, the refresh command and the usage rights for every dataset.

**Check the results**

| Report | Location |
|---|---|
| India audit | `data/validation/india/audit_summary.json` (must show 0 FAIL) |
| SEC reconciliation | `data/validation/fundamentals/sec_balance_sheet_reconciliation.csv` |
| Lake catalog | `data/validation/catalog/lake_catalog.md`: every dataset with files, rows, date range, schema and cadence |

## 4. Releases (freeze before using the data)

A release is a checksummed manifest of every file in the lake. The API and Quantera only read a frozen release, so results are reproducible.

```bash
python scripts/freeze_release.py --tag lake-2026.12           # refuses if jobs are running or the audit fails
python scripts/freeze_release.py --verify lake-2026.12        # 0 missing / 0 changed = intact
```

- Releases are written to `data/releases/<tag>/`.
- Current release: **`lake-2026.12`** (234,817 files, 39.5 GB).
- Tag format: `lake-YYYY.MM`; cut a new release monthly, or after any schema change.
- The next data refresh changes some files, so after a refresh the old release no longer verifies. The workflow is: **update → finalize → freeze a new tag → serve the new tag**.

## 5. Backup

```bash
scripts/backup_lake_local.sh "<backup_dir>"            # dry run: shows what would be copied
scripts/backup_lake_local.sh "<backup_dir>" --execute  # copy, then checksum-verify
```

- The backup only adds files; it never deletes from the backup. It works on NTFS or exFAT disks.
- Results are logged in `data/validation/backup_log.txt`. A good run ends with `OK: backup matches data/`.
- Run it after every finalize. Keep an offline copy too, since both disks are in the same machine.

## 6. Using the API

```bash
python -m api.cache --build      # once per release: builds the fast price cache (about 20 s, 1.3 GB)
python -m api                    # http://127.0.0.1:8001, latest release, 4 workers
```

Interactive documentation is at **http://127.0.0.1:8001/docs**.

**Options:** `--release lake-2026.12`, `--mode redistribution`, `--port 8001`, `--workers 4`, `--drift-check full`.

The API refuses to start if any file in the release is missing or changed (`RELEASE_DRIFT`).

**Examples**

```bash
curl "localhost:8001/v1/instruments?query=RELIANCE"
curl "localhost:8001/v1/prices/daily?symbol=RELIANCE&exchange=NSE&start=2024-01-01&end=2024-12-31&adjust=split"
curl "localhost:8001/v1/prices/daily?bse_code=500325&start=2024-01-01"
curl "localhost:8001/v1/derivatives/options/greeks?symbol=NIFTY&start=2025-06-05&end=2025-06-05&quality=ok_only"
curl "localhost:8001/v1/derivatives/options/surface?symbol=NIFTY&date=2025-06-05"
curl "localhost:8001/v1/fundamentals/statements?source=sec&ticker=AAPL&as_of=2024-12-31"
curl "localhost:8001/v1/fundamentals/statements?source=nse&symbol=ACC&period_type=Quarterly"
curl "localhost:8001/v1/funds/etf-holdings?symbol=XLK&as_of=2025-01-01"
curl "localhost:8001/v1/macro/series?series_id=INDIR3TIB01STM"
curl "localhost:8001/v1/prices/daily?symbol=HDFCBANK&exchange=NSE&series=EQ&adjust=total"   # full history across ISIN changes
curl "localhost:8001/v1/corporate-actions/dividends?symbol=LT&exchange=NSE"
curl "localhost:8001/v1/universes?index=NIFTY%2050"                                        # constituents + industry (snapshot)
curl "localhost:8001/v1/instruments/history?symbol=ZYDUSLIFE"                              # renames and ISIN changes
curl "localhost:8001/v1/catalog"
```

**Conventions**

- **Point-in-time `as_of`.** Only data known on that date is used: filings filed by then, and split or bonus adjustments with ex-date by then.
- **Prices.** Prices are as traded by default. `adjust=split` applies split and bonus adjustments; `adjust=total` also applies cash dividends (NSE-traded Indian stocks only). NSE histories include a company's earlier ISINs and symbols; `id_match` on each row says how it was linked.
- **Paging.** The default limit is 10,000 rows. Pass the `next_cursor` value back as `cursor=`.
- **Arrow output.** `format=arrow` returns an Arrow IPC stream for bulk pulls.
  ```python
  import httpx, pyarrow as pa
  r = httpx.get("http://127.0.0.1:8001/v1/derivatives/fo/daily?symbol=NIFTY&start=2025-01-01&end=2025-01-31&format=arrow&limit=100000")
  table = pa.ipc.open_stream(r.content).read_all()
  ```
- **Response metadata.** Every response states its `release`, `dataset`, `schema_version` and `usage_rights`, plus `notes` about how the data was handled.
- **Errors.** Errors return `{"error": {"code", "message", "details"}}`. For example, `AMBIGUOUS_IDENTIFIER` means a symbol is listed on both NSE and BSE; add `exchange=NSE` or `exchange=BSE`.

Full design, endpoint list and known limitations: [docs/api_design.md](docs/api_design.md).

**Validate**

```bash
pytest tests/api -q                                   # contract, point-in-time, known answers, drift, licensing
python -m api & python scripts/api_loadtest.py        # p95 latency vs targets
```

## 7. Rules for contributors

1. **Bronze is immutable.** Never edit or delete raw files; fix problems in Silver or Gold.
2. **Never drop data silently.** Flag it instead (`quality_flag`, `iv_status`, `is_duplicate_fact`, quarantine).
3. **New pipelines follow the existing pattern:**
   - registry linkage;
   - `observation_id`;
   - quarantine for bad rows;
   - `year=`/`month=` partitions;
   - a coverage ledger;
   - atomic writes (`.tmp`, then rename).
4. **No hard-coded company adjustments.** Never sum XBRL dimensional members into totals.
5. **Respect providers.**
   - Keep the rate limits (`--pause`).
   - SEC requires a contact User-Agent (set in the SEC modules).
   - Do not get around bot protection. For example, MCX blocks automated access, so it is not collected.
6. **Licensing.** NSE, BSE, NSE Indices and Yahoo data are for internal research only; redistribution needs licences. SEC data is public domain. Run the API with `--mode redistribution` for anything outside internal use.
7. **Quantera reads through the API only**, never Parquet paths directly.

## 8. Further reading

- `global_financial_data_lake_handover.md`: project plan and status (§14 is the latest).
- `docs/india_data_lake.md`: India datasets, methods (including the IV and Greeks model), known gaps and usage rights.
- `docs/api_design.md`: the API design and its validation results.


python -m api                      # http://127.0.0.1:8001, docs at /docs
pytest tests/api -q                # 35 passed
python scripts/api_loadtest.py     # speed test, run while the API is up

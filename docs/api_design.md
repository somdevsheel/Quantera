# Quantera Data-Access API: Design

Status: **v1 implemented and validated** (12 Nov 2026) against release `lake-2026.11`
(frozen 11 Nov 2026; 234,798 files, 39.5 GB, India audit 37 PASS / 0 FAIL / 2 WARN).
Code is in `api/`, tests in `tests/api/`. All §8 checks pass (§10). Quantera integration
(handover §7 Step F) can start. §9 decisions use the proposed defaults until the owner says otherwise.

## 1. Goals and non-goals

**Goals**
- One read-only interface over the frozen lake, so Quantera never reads Parquet paths directly.
- Every response is reproducible: it is pinned to a release and returns as-of results without look-ahead.
- Licensing is enforced in code. Data that may not be redistributed is never served beyond internal use.

**Non-goals**
- No writes, no ingestion triggers, no live or real-time market data.
- No cloud deployment yet: storage is local-only (handover §12).

## 2. Architecture

```
Quantera ──HTTP/JSON or Arrow──▶ FastAPI service ──▶ DuckDB (in-process, read-only)
                                     │                      │
                                     │                      └─▶ data/{silver,gold}/**.parquet
                                     └─▶ release registry: data/releases/<tag>/release_manifest.parquet
```

- **FastAPI with in-process DuckDB.** DuckDB reads Parquet directly with partition pruning on `year=`/`month=`, so there's no database server to run or keep in sync.
- **Release pinning.** At startup the service loads `release_manifest.parquet` for the configured tag and builds DuckDB views only over the files listed there. Files added after the freeze stay invisible until a new release is cut. The startup check is `freeze_release.py --verify <tag>`, and the service refuses to start if any file is missing or changed.
- **Adapters.** Each dataset family is one module that maps lake columns to the API schema (handover rule 8). Provider-specific logic stays in the pipelines.

## 3. Conventions

| Topic | Rule |
|---|---|
| Identifiers | `instrument_id` from the registry is canonical. Exchange codes are also accepted: `?symbol=RELIANCE&exchange=XNSE`, `?bse_code=500325`. F&O uses `contract_id` (`NSEFO:OPT:NIFTY:2025-06-05:24900:CE`). SEC data uses `cik`. ETF holdings use `series_id`. |
| Dates | ISO `YYYY-MM-DD`. The trading date is in the exchange's local time zone. Timestamps are UTC. |
| Point-in-time | `as_of=YYYY-MM-DD` on fundamentals, corporate actions and adjustment factors only returns facts filed or announced on or before that date (filing/acceptance date, `is_latest_filing` at that time). |
| Prices | Raw (as traded) by default. `adjust=split\|total` applies the Gold adjustment factors as of `as_of`, never factors announced later. |
| Units | Values are returned in source currency with a `currency` field. INR values stay in INR. Conversion happens only on request (`fx=USD`), using the stated FX series. |
| Quality flags | Rows are never silently dropped. Fields such as `iv_status`, `check_status`, `registry_status`, `source_repair` and `period_source` are returned as-is, and `?quality=ok_only` filters on request. |
| Lineage | Every response carries `release`, `dataset`, `schema_version` and, where the row has them, `source_file`/`accession`. |
| Paging | Cursor-based (`next_cursor`), with a default limit of 10,000 rows. `format=arrow` streams Arrow IPC for bulk pulls. |

## 4. Endpoints (v1)

| Endpoint | Source dataset(s) | Notes |
|---|---|---|
| `GET /v1/releases`, `/v1/releases/{tag}` | `data/releases/*/release.json` | Lists releases with their audit results. |
| `GET /v1/catalog` | `lake_catalog.csv`, `lake_schemas.csv` | Dataset list, schemas, cadence and usage rights. |
| `GET /v1/instruments?query=` | registry `instruments`, `exchange_listings` | Search and resolve identifiers. |
| `GET /v1/prices/daily` | NSE/BSE equity Silver; Yahoo Silver for global symbols | `instrument_id \| symbol+exchange`, `start`, `end`, `adjust`, `as_of`. |
| `GET /v1/indices/daily` | NSE indices | `index_id` (`NSEIDX:NIFTY_50`, `NSEIDX:INDIA_VIX`). |
| `GET /v1/derivatives/fo/daily` | NSE F&O Silver | Filters: `symbol`, `expiry`, `instrument_class`, `strike`, `option_type`. |
| `GET /v1/derivatives/options/greeks` | `gold/india/derivatives/nse_option_greeks` | Adds `forward`, `forward_source`, `rate_series`, `iv_status`. |
| `GET /v1/derivatives/options/surface` | same | One day's IV by expiry × log-moneyness (`iv_status=OK` only). |
| `GET /v1/funds/nav` | AMFI NAV | `scheme_code`, date range. |
| `GET /v1/funds/etf-holdings` | `gold/funds/etf_holdings` | `symbol \| series_id`, `report_date` (latest report on or before `as_of`). |
| `GET /v1/corporate-actions` | India Silver/Gold; global Gold | Events plus `price_factor`/`combined_price_factor`. |
| `GET /v1/calendar/trading` | `gold/india/calendar` | Trading days and holidays per exchange. |
| `GET /v1/calendar/events` | NSE event calendar | Results dates, board meetings, AGMs. |
| `GET /v1/fundamentals/statements` | SEC Gold statements; NSE key financials | `as_of` honoured; includes the balance-sheet reconciliation status. |
| `GET /v1/fundamentals/facts` | SEC filing facts; NSE XBRL facts | Raw facts with contexts and dimensions, unsummed (handover rule 5). |
| `GET /v1/macro/series` | FRED India + global | `series_id`, `start`, `end`; carries `original_source`. Release dates come later, via ALFRED vintages. |

## 5. Licensing enforcement

The usage rights for each dataset come from `reference/dataset_cadence.csv` and appear in `/v1/catalog`. The API carries a deployment mode:

| Mode | Behaviour |
|---|---|
| `internal_research` (default, only mode for now) | All datasets are served, and each response includes a `usage_rights` header. |
| `redistribution` | Datasets marked "no redistribution", "licence needed" or "personal/research use only" (Yahoo, NSE/BSE exchange data, NSE Indices) return `403 LICENCE_REQUIRED`. SEC (public domain), FRED (per-series terms), AMFI (with attribution) and derived data from public sources are served. |

## 6. Performance targets (single local machine)

- **Single-instrument daily history (20 years):** p95 under 150 ms, using partition pruning plus a per-instrument cache.
- **One day's F&O chain for one underlying:** p95 under 300 ms. One file per day keeps this fast.
- **Bulk pulls** (for example a whole year of options Greeks) stream as Arrow and are limited only by disk speed.
- **Hot views**, such as the latest release's equity daily data, can be materialised into a DuckDB file built at release time. That file can always be rebuilt from Parquet.
  - *Implemented as* `python -m api.cache --build`: NSE and BSE equity daily data re-sorted by instrument key into `data/releases/<tag>/api_cache/*.parquet` (1.3 GB, 20 s).
  - It is built only from the release's own files, with row counts checked against them.
  - The API uses it only when its recorded source-file hash matches the release; otherwise it falls back to the per-day files.
  - A test checks that the cached and uncached paths return identical rows.
- **Server processes.** Run with 4 uvicorn workers (`python -m api`, the default). With one process, JSON encoding of long histories queues behind the Python GIL.

## 7. Errors

The error envelope is `{"error": {"code", "message", "details"}}`:

| Code | Error |
|---|---|
| 400 | `INVALID_PARAMETER` |
| 404 | `UNKNOWN_INSTRUMENT` / `NO_DATA_IN_RANGE` |
| 403 | `LICENCE_REQUIRED` |
| 409 | `RELEASE_DRIFT` (lake files differ from the pinned release) |
| 422 | `AMBIGUOUS_IDENTIFIER` (for example a symbol listed on both NSE and BSE without `exchange`) |

## 8. Validation before Quantera integration

1. **Contract tests.** For each endpoint, compare responses with direct DuckDB queries on the same release. Results must match row for row.
2. **Point-in-time tests.**
   - Set `as_of` before an ex-date and confirm the factor is not applied.
   - Set `as_of` before a 10-Q filing date and confirm the statement is not returned.
   - Restated filings return the version that was current at `as_of`.
3. **Known-answer tests.**
   - AAPL statements.
   - BLK reconciliation at 2026-06-30 (difference 0).
   - ACC four quarters sum to the annual filing.
   - NIFTY 30-day ATM IV tracks India VIX. *Revised:* the original "within 2 vol points" was too tight. India VIX is a model-free variance index that includes the skew premium, so it sits above ATM IV. Measured on 38 dates in 2023–2026, VIX − ATM30 has a median of +1.1, a 5th percentile of −0.6, a 95th percentile of +3.6 and a maximum of +4.8 vol points. The test asserts each day is in (−2, +5) and the mean gap is in [0, 3].
   - XLK holdings weights sum to about 100%.
4. **Release drift.** Modify a copy of one file and confirm the service refuses to start (`RELEASE_DRIFT`).
5. **Licensing.** In `redistribution` mode, every Yahoo- or NSE-sourced endpoint returns 403.
6. **Load test.** Meet the p95 targets in §6 with 8 concurrent clients.

## 9. Open decisions (for the owner)

- **Transport.** REST with JSON and Arrow (proposed), or also Arrow Flight or gRPC for bulk pulls?
- **Authentication.** None while it runs only on localhost (proposed), or API keys once Quantera runs elsewhere?
- **Global equity source.** Keep Yahoo for the 153 global equities (research only), or license a provider before any external use?
- **Release cadence.** Proposal: monthly release, plus an ad-hoc release after any schema change.
- **Serving older releases.** A release is a manifest, not a copy. The next refresh rewrites single-file Gold datasets (for example `etf_holdings.parquet` and `sec_statements.parquet`) and the registry, so the previous release then fails the drift check and can no longer be served. The workflow is refresh → audit → freeze a new tag → serve the new tag. If old releases must stay servable, snapshot the mutable single files (about 400 MB) into `data/releases/<tag>/` at freeze time.

## 10. Implementation (v1) and validation results

**Run**

```
python -m api                                   # 127.0.0.1:8000, latest release, internal_research, 4 workers
python -m api --release lake-2026.11 --mode redistribution
python -m api.cache --build                     # once per release (hot price cache, §6)
pytest tests/api -q                             # §8 items 1-5
python scripts/api_loadtest.py                  # §8 item 6, against a running server
```

OpenAPI docs are served at `/docs`.

**Layout**

| Module | Contents |
|---|---|
| `api/release.py` | Release pinning and the drift check |
| `api/datasets.py` | Dataset → manifest files, usage rights |
| `api/identifiers.py` | Identifier resolution |
| `api/query.py` | Paging, JSON/Arrow output |
| `api/cache.py` | Hot price cache |
| `api/adapters/*.py` | One module per dataset family |

**Startup.** Startup takes about 2 s. It runs the fast drift check (size and mtime against the manifest, hashing only files whose mtime moved) and refuses to start on drift. Use `QUANTERA_DRIFT_CHECK=full` to hash every file.

**Validation (12 Nov 2026).** `pytest tests/api` gives 35 passed. They cover:

- **Contract:** results match direct DuckDB queries for NSE prices, BSE prices (including unlinked rows), the F&O chain, Greeks, Arrow vs JSON, and the cache vs per-day files.
- **Point-in-time:**
  - RELIANCE 2024-10-28 bonus: not applied with `as_of` 2024-10-27, applied with `as_of` 2024-10-28.
  - NVDA 10:1 split.
  - BLK 10-Q invisible before its filing date.
  - ACC restated quarter: the correct version is returned before and after the revision.
  - ETF report filed by `as_of`.
  - Future corporate actions hidden.
- **Known answers:** BLK 2026-06-30 difference 0; AAPL latest 10-K balanced; ACC FY24 quarters sum to the annual fact (0.0001%); NIFTY ATM30 vs VIX; XLK weights sum to about 100%.
- **Drift:** a tampered manifest refuses startup.
- **Licensing:** redistribution mode returns 403 for NSE, BSE, Yahoo and derived data, and 200 for SEC, AMFI and FRED.
- **Errors:** 400, 404 and 422 return the error envelope.
- **Strict JSON:** no NaN anywhere.

**Load test** (`scripts/api_loadtest.py`, 8 clients × 200 requests, 4 workers):

| Scenario | p95 | Target |
|---|---|---|
| 20-year single-stock history | 97 ms | 150 ms |
| Split-adjusted 20-year history | 95 ms | 150 ms |
| F&O chain, one day | 36 ms | 300 ms |
| Greeks chain, one day | 57 ms | 300 ms |
| SEC statements | 43 ms | 150 ms |

**Behaviour the API adds on top of the lake** (documented in responses as `notes`)

- **NSE equities before about mid-2011.** The bhavcopy has no ISIN, so `instrument_id` is NULL. These rows are matched on the current NSE symbol and flagged `id_match=SYMBOL_PRE_ISIN`. Symbol changes before 2011 are not followed. *Lake fix (next release):* backfill `instrument_id` from symbol history.
- **BSE.** 3.1M bhavcopy rows are `UNLINKED` in the registry. They are matched on scrip code (`id_match=SCRIP_CODE`), not dropped.
- **Yahoo global prices.** They are already split-adjusted at retrieval, even though Silver labels them `UNADJUSTED_OHLC_WITH_ADJ_CLOSE`. The API reverses the adjustment with the release's split table, so `adjust=none` gives prices as traded (NVDA 2024-06-07 = 1,208.88) and `adjust=split` re-applies splits only up to `as_of`. *Lake fix:* correct the label (or store raw prices) in the next release.
- **`adjust=total`** is refused (400). The release has no dividend factors for NSE or BSE.
- **SEC tickers spanning several CIKs** (BLK: predecessor 0001364742, then 0002012383) return all CIKs. `cik_role` tells them apart.
- **NSE key financials.** The lake's `is_latest_filing` looks at all filings, including later ones. The API drops it and recomputes the latest version per period as of `as_of`.
- **FRED series** are the latest revision at retrieval and are not point-in-time (no vintages yet). The event calendar's `is_future` reflects retrieval time. Corporate-action announcement dates are not in the release, so events are filtered by ex-date.
- **Global prices** cover only the 7 symbols in `silver/market`. The other Yahoo instruments exist only in Bronze and are not served.

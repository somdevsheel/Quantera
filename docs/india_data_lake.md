# India Data Lake — Datasets, Sources, Methodology, Gaps

Status date: 2026-11-09. Measured coverage lives in
`data/validation/india/coverage_report.csv` and `quality_report.csv`
(regenerate with `python validation/india_lake_audit.py`). This file explains
what each dataset is; the reports say how much of it exists.

## 1. Datasets

| Dataset | Pipeline | Source | History available | Silver path |
|---|---|---|---|---|
| NSE equity daily (all series) | `pipelines/india_nse_historical_backfill.py` (≤2024‑07‑05), `india_nse_udiff_backfill.py` (≥2024‑07‑08) | NSE CM bhavcopy (legacy + UDiFF) | 2000‑01 → present | `data/silver/india/equity_prices/nse_*equity_prices_YYYYMMDD.parquet` |
| BSE equity daily OHLCV (all scrips) | `pipelines/india_bse_bhavcopy_backfill.py` + ledger `india_bse_bhavcopy_coverage_ledger.py` | BSE equity bhavcopy (`EQddmmyy_CSV.ZIP`, UDiFF CSV) | 2006 → present | `data/silver/india/equity_prices/bse_bhavcopy/year=YYYY/month=MM/` |
| BSE delivery enrichment (delivery qty/%, weighted price, spreads) | `india_bse_historical_backfill.py` + `india_bse_coverage_ledger.py` | BSE StockPriceSearch API (per scrip/month) | pilot 1,000 scrips, 2025‑11 → 2026‑10 | `data/silver/india/equity_prices/bse/year=/month=/` |

**BSE hybrid design.** Bhavcopy is the backbone for BSE price history (one
file per day, all scrips). It follows the API normalizer's conventions:
registry linkage (`exchange_listings`, XBOM) for `instrument_id` /
`exchange_listing_id` with `registry_status` LINKED/UNLINKED,
`observation_id` per scrip/day, and duplicate / invalid-OHLC rows go to
`data/quarantine/...` instead of Silver. On overlapping days the two datasets
agree exactly (OHLC, volume, trades, turnover and registry IDs: 788/788 scrips
on 2026‑10‑30). The per-scrip API is used only to add delivery data; the
ledger column `delivery_status` (ENRICHED / PARTIAL / NOT_ENRICHED) shows
where enrichment is still missing. UNLINKED scrips are mainly debt (series F),
government securities (G) and ETFs (INF ISINs), which the equity registry
does not contain.
| NSE indices daily (≈100–170 NIFTY indices incl. India VIX; OHLC, P/E, P/B, div yield) | `india_nse_indices_backfill.py` | NSE `ind_close_all_DDMMYYYY.csv` | 2012 → present | `data/silver/india/indices/nse/year=YYYY/` |
| NSE futures & options daily | `india_nse_fo_backfill.py` | NSE F&O bhavcopy (legacy + UDiFF) | 2000‑06 → present | `data/silver/india/derivatives/nse_fo/year=YYYY/` |
| Mutual-fund NAV (all AMFI schemes, incl. ETFs) | `india_amfi_nav_backfill.py` | AMFI NAV history report | 2006‑04 → present | `data/silver/india/mutual_funds/nav/year=YYYY/` |
| Corporate actions | `india_nse_corporate_actions.py` | NSE corporate-actions API | 2000 → present | `data/silver/india/corporate_actions/nse_corporate_actions.parquet` |
| Price adjustment factors (splits, bonuses) | same | derived | 2000 → present | `data/gold/india/corporate_actions/nse_price_adjustment_factors.parquet` |
| INR FX (USD, EUR, GBP, JPY, CNY, SGD) | `india_fred_series_backfill.py` | FRED / Federal Reserve H.10 | USD/INR 1973 →; EUR/INR 1999 → | `data/silver/india/fx/inr_reference_rates.parquet` |
| Rates (call money, 3M interbank, 10Y G‑sec yield, discount rate) | same | FRED (OECD MEI, IMF IFS) | monthly; 10Y from 2011 | `data/silver/india/rates/fred_series.parquet` |
| Macro (CPI, GDP, IIP, M3, trade, reserves, CLI) | same | FRED (OECD, IMF, World Bank) | varies per series | `data/silver/india/macro/fred_series.parquet` |
| Company financial results (all NSE filings, quarterly + annual, standalone + consolidated) | `india_nse_financial_results.py` | NSE financial-results API + XBRL instances | metadata 2005 →; XBRL ≈2015 → | facts `data/silver/india/fundamentals/xbrl_facts/symbol=*/`; key metrics `data/gold/india/fundamentals/nse_key_financials.parquet` |
| Corporate event calendar (board meetings: results, dividends, fund raising …, incl. announced future dates) | `india_nse_event_calendar.py` | NSE event-calendar API | 2008 → +1 month | `data/silver/india/calendar/nse_event_calendar.parquet` |
| G‑Sec / bond indices (Nifty 4‑8y, 8‑13y, 11‑15y, 15y+, 10y benchmark, composite G‑Sec, BHARAT Bond) | `india_nse_indices_backfill.py` | NSE index file | 2012 → | inside NSE indices dataset |
| Holidays / trading calendar | `india_trading_calendar.py` | NSE holiday master + observed sessions | per dataset | `data/silver/reference/india/nse_holidays.parquet`, `data/gold/india/calendar/` |

Series catalog for FRED: `reference/series/india_fred_series.csv`.

Every pipeline is restartable: existing Bronze files are revalidated rather
than re-downloaded, existing Silver files are skipped, and each attempt is
appended to a log in `data/staging/india/*_log.csv` with HTTP status, byte
count and SHA‑256 of the raw payload.

## 2. Identifiers

* `instrument_id` = `IN:<ISIN>` (equities, corporate actions).
* `exchange_listing_id` = `BSE:<scrip code>`; NSE uses `symbol` + `security_series`.
* Indices: `index_id` = `NSEIDX:<NORMALISED_NAME>` (e.g. `NSEIDX:NIFTY_50`, `NSEIDX:INDIA_VIX`).
* F&O: `contract_id` = `NSEFO:<FUT|OPT>:<symbol>:<expiry>:<strike>:<CE|PE|FUT>`;
  UDiFF rows additionally keep `exchange_contract_id`.
* MF: AMFI `scheme_code`, plus ISINs (growth/payout and reinvestment).

## 3. Units and conventions

* Prices in INR, as printed by the exchange (**unadjusted**).
* NSE F&O: legacy files report `contracts` and value in lakhs — stored as
  `contracts` and `traded_value_inr = VAL_INLAKH × 1e5`. UDiFF files report
  `traded_quantity`, `traded_value_inr`, `lot_size`, `underlying_price`.
  The two are not silently unified; use `source_format` to tell them apart.
* Index turnover is in INR crore (`turnover_inr_crore`), as published.
* FX pairs are INR per 1 unit of base currency. Only USD/INR is a direct
  observation; others are `method = CROSS_VIA_USD` from H.10 rates.
* FRED rows keep `units`, `frequency`, `original_source` per series.

## 4. Corporate-action adjustment methodology

Silver prices are raw. Adjusted prices are computed on read using the Gold
factor table:

* SPLIT, face value F_old → F_new: `price_factor = F_new / F_old`
* BONUS a:b (a new shares per b held): `price_factor = b / (a + b)`
* Several events on one ex-date compound: use `combined_price_factor`
  (one row per event, same combined value on each).
* To adjust: multiply every price strictly before `ex_date` by the product of
  `combined_price_factor` over all later distinct ex-dates; divide volumes
  by the same product.
* Dividends are **not** applied to prices; they are kept with parsed
  `dividend_per_share_inr` / `dividend_pct_of_fv` for total-return series.
* Rights, buybacks, demergers, mergers and capital reductions are recorded
  but not auto-adjusted (terms are not machine-readable in the source).

Validation: for every split/bonus ex-date since 2006 where both the previous
and ex-date NSE close exist (600 events), the observed close ratio divided by
`combined_price_factor` lies within ±25% for 99% of events and ±10% for 88%
(residual = normal one-day price move + a few data errors).

## 4a. Option implied volatility and Greeks

`pipelines/india_nse_option_greeks.py` → `data/gold/india/derivatives/nse_option_greeks/year=YYYY/`
(one file per day) and `nse_option_forwards/` (one forward per symbol × expiry × day).

* Model: Black-76 on the option's own-expiry forward, European exercise.
* Forward: put-call parity at the strike with the smallest |C − P| among strikes where
  both legs traded, `F = K + (C − P)/DF`; fallback is the same-expiry future close
  (`forward_source`).
* Rate: India 3-month interbank (`INDIR3TIB01STM`) from Nov 2011, call money before;
  continuous `ln(1 + r)`; `T` = calendar days / 365.
* Greeks w.r.t. the forward: delta, gamma, vega per 1 vol point, theta per calendar day.
* `iv_status`: OK, AMERICAN, EXPIRY_DAY, NO_FORWARD, BELOW_INTRINSIC, ABOVE_MAX,
  NO_CONVERGENCE. Untraded options are counted (NO_TRADE) in
  `data/validation/india/nse_option_greeks_ledger.csv` but not written.
* Check: NIFTY ≈30-day ATM IV tracks India VIX 0.5–1.3 vol points below (Jun 2025),
  as expected since VIX includes skew. Call and put IV at the same strike agree within 0.2 pts.

## 5. Known gaps and limitations (not hidden)

* **BSE before 2006**: daily bhavcopies are not published at the public URL.
  Only the per-scrip API could fill it (~1.6M requests); not attempted.
* **BSE delivery data**: only in the per-scrip API dataset (pilot 1,000
  scrips, 12 months). Bhavcopy has no delivery columns. Ledger: 10,279
  scrip-months ENRICHED, 1,037,165 NOT_ENRICHED.
* **BSE source defects**: EQ291221 has two records on one line; the reader
  splits them and flags `source_repair = SPLIT_MERGED_LINE` (Bronze is not
  modified). 2,008 rows with invalid OHLC are in quarantine.
* **BSE API source duplicates**: for scrip 500271 (Max Financial) in Jan-2000
  the BSE API returns two unlabeled rows on 6 days (one large-volume, one tiny;
  most likely demat vs physical). Both are kept; the audit reports them as a
  documented WARN (`KNOWN_SOURCE_DUPLICATES`), not as a pipeline defect.
* **BSE registry links**: the registry holds current listings only, so
  10,594 historical scrips (delisted, debt, ETFs) are UNLINKED; 5,772 LINKED.
* **BSE legacy ISINs**: legacy files have no ISIN. ISIN is mapped from the
  current BSE security master, so delisted scrips have null ISIN
  (≈27% of rows in 2007, ≈12% in mid‑2024). Needs a historical scrip master.
* **NSE indices before 2012**: needs niftyindices.com history; BSE indices
  (SENSEX family) only via the earlier Yahoo pipeline (3 indices).
* **F&O implied volatility and Greeks**: computed (not sourced) in
  `gold/india/derivatives/nse_option_greeks/` — see §4a. Only options that traded
  get an IV; NSE settlement prices for untraded strikes are model output. American
  stock options (CA/PA, before 2011) are flagged `AMERICAN`, not priced. The rate
  is a monthly average (small look-ahead within the month).
* **FRED vintages**: the public CSV gives only the latest vintage. Release
  dates / revisions need ALFRED (API key). Each run stores a dated Bronze
  snapshot as a vintage proxy. Several OECD India series are discontinued
  (M3 2018, discount rate 2022, IIP 2023/2024, CPI 2025) — RBI DBIE / MOSPI
  sources are required to extend them.
* **Fundamentals**: XBRL exists only for filings from ≈2015; older filings
  have metadata only. Key metrics come from the primary (non-dimensional)
  context whose period exactly equals the filing period — no fallback to
  another period. Filer defects are handled and flagged in `period_source`:
  undefined contexts (`OneD` = reporting period, `FourD` = FY to date) and
  mis-dated contexts (period taken from the context's own
  DateOfStart/EndOfReportingPeriod facts; original kept in
  `context_period_*`). Revisions are all kept; `is_latest_filing` marks the
  newest. Check: ACC FY24 four quarters sum exactly to the annual filing.
  Bank / NBFC / insurance taxonomies use different line items — the key
  metric map covers common Ind‑AS concepts; all other facts stay in Silver.
* **MCX commodities**: mcxindia.com denies automated access (HTTP 403,
  CDN block). Not circumvented; needs an MCX data licence / vendor feed.
* **Bonds**: no G‑Sec / corporate bond prices or yield curve (CCIL, FBIL,
  RBI). Proxies available: Nifty G‑Sec indices (2012 →), 10Y G‑Sec yield and
  call/3M rates monthly (FRED).
* **Not started**: ETF holdings / AUM / expense ratios (AMC portfolio
  disclosures), macro release calendar (RBI MPC / MOSPI dates).
* **Corporate actions**: NSE-only; free-text subjects are classified by rules,
  unparsed amounts are kept with nulls (counts printed by the pipeline).

## 6. Usage rights (review before any commercial use)

Technical access does not grant redistribution rights.

* **NSE / BSE** bhavcopies, index files, corporate actions: published on the
  exchange websites for information; exchange terms restrict commercial
  redistribution and display of data — a data-vendor licence is needed for
  commercial products. Index values (NIFTY) are © NSE Indices Ltd.
* **AMFI** NAV: published for public information; attribute AMFI.
* **FRED**: public-domain series (H.10) are free to use; OECD / IMF / World
  Bank series carry their own terms — check each series' notes on FRED.
* Store data for internal research; obtain licences before redistribution.

## 7. Operating commands

```bash
# daily / incremental (safe to rerun)
python pipelines/india_nse_udiff_backfill.py   --start <d> --end <d>
python pipelines/india_bse_bhavcopy_backfill.py --start <d> --end <d>
python pipelines/india_nse_indices_backfill.py  --start <d> --end <d>
python pipelines/india_nse_fo_backfill.py       --start <d> --end <d>
python pipelines/india_amfi_nav_backfill.py     --start <YYYY-MM> --end <YYYY-MM>
python pipelines/india_nse_corporate_actions.py --start <YYYY-MM>
python pipelines/india_fred_series_backfill.py
python pipelines/india_trading_calendar.py

# audit + local backup (S3 parked: AWS account closed Nov 2026)
python validation/india_lake_audit.py
scripts/backup_lake_local.sh "/media/somdevsheel/New Volume11/quantera_backup"            # dry run
scripts/backup_lake_local.sh "/media/somdevsheel/New Volume11/quantera_backup" --execute  # copy + verify
# S3 later: python scripts/sync_india_to_s3.py [--execute] (needs a new AWS account)
```

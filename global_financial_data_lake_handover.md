# Global Financial Data Lake

## Project Handover and Restart Reference

**Status date:** 31 October 2026\
**Immediate priority:** Complete the Indian financial market data lake
first.\
**Downstream application:** Quantera. The data lake is standalone
infrastructure. Complete and audit it first, build the data-access API
second, and integrate that API into Quantera last.

## 1. Project objective

Build a provider-independent Global Financial Data Lake with broad
historical financial and market datasets. Target approximately 20 years
of history wherever reliable data is available. Actual history varies by
instrument, provider, filing history, and dataset. Record actual
coverage and gaps; never fabricate missing history or claim 20 years
where unavailable.

### Architecture

-   **Bronze:** Immutable raw provider data, source payloads, filing
    XML, and original downloads.
-   **Silver:** Normalized, typed, deduplicated, point-in-time records
    with source lineage and preserved dimensions.
-   **Gold:** Curated research-ready datasets and derived outputs.
-   **Reference data:** Instrument, issuer, exchange, country, currency,
    sector, industry, and provider-symbol masters.
-   **Controls:** Manifests, ingestion registry, checksums, run IDs,
    schema versions, logs, validation reports, and quarantine records.

### Technical setup

-   S3 bucket: `quant-global-data-lake-prod`

-   AWS region: `ap-southeast-2`

-   Local processing: Ubuntu laptop

-   No EC2, ECS, or RDS requirement for the initial implementation

-   Parquet/ZSTD, Python, pandas, PyArrow, Polars, DuckDB, SQLite

-   AWS authentication through AWS CLI login / assumed role

-   `.env` currently contains:

    ``` env
    AWS_REGION=ap-southeast-2
    S3_BUCKET=quant-global-data-lake-prod
    ```

-   Do not store AWS credentials in `.env`.

-   Respect provider rate limits and licensing. API accessibility does
    not automatically grant commercial storage, display, or
    redistribution rights.

## 2. Completed or substantially established work

  -----------------------------------------------------------------------
  Workstream              Current state           Evidence / notes
  ----------------------- ----------------------- -----------------------
  Global equities daily   Initial universe        153 instruments;
  OHLCV                   completed               763,066 records

  US-focused ETF daily    Initial universe        20 instruments; 95,969
  OHLCV                   completed               records

  Global index daily      Initial universe        25 indices; 128,954
  OHLCV                   completed               records

  SEC Company Facts raw   Completed for US        80 US companies; all 80
  ingestion               universe                S3 objects verified

  SEC Company Facts       Initial normalization   Six output categories;
  Silver normalization    completed               80/80 processed

  S3 storage              Established             Bucket access,
                                                  versioning, SSE-S3
                                                  checked

  SQLite ingestion        Established             198 entries across
  registry                                        equity, ETF, and index

  Instrument master       Established             198 instruments across
                                                  equity, ETF, and index

  Global equity universe  Established             153 equities across 10
                                                  countries

  Filing-level XBRL       Working prototype       Tested on one BlackRock
  extractor                                       filing

  SEC filing discovery    Initial discovery       3,027 filing records
                          completed               across 80 US companies

  Historical SEC          Implemented but not yet First run added zero
  submissions collector   verified                records due to parser
                                                  bug; patched version
                                                  needs rerun
  -----------------------------------------------------------------------

### Verified initial market-data totals

  Asset class     Instruments       Records
  ------------- ------------- -------------
  Equities                153       763,066
  ETFs                     20        95,969
  Indices                  25       128,954
  **Total**           **198**   **987,989**

Registry: 198 successful entries, zero failures. S3 `head-object` audit
passed for all 198 registered objects. These totals describe the initial
daily-bar universes, not comprehensive coverage of every global market.

### Key universe files

-   `reference/universes/global_equity_universe.csv`
-   `reference/universes/companies/global_company_universe.csv`
-   `reference/universes/etf/us_etf_candidates.csv`
-   `reference/universes/etf/us_etf_validation.csv`
-   `reference/universes/indices/global_index_candidates.csv`
-   `reference/universes/indices/global_index_validation.csv`

The initial global equity universe contains 153 equities across 10
countries. Only seven Indian equities are present, so it is not a
complete Indian market universe.

## 3. Immediate priority: complete the Indian market

**Do not resume the remaining global-market workstreams until the India
phase is completed and audited.**

### Existing Indian coverage

-   Seven Indian equities in the global equity universe.
-   NIFTY 50, NIFTY Bank, and SENSEX in the 25-index universe.
-   Initial daily history for those indices ingested through the
    existing Yahoo Finance pipeline.
-   ETF universe is US-focused, not a comprehensive Indian ETF universe.
-   Indian company fundamentals, corporate actions, derivatives, bonds,
    FX, commodities, and macroeconomic datasets are not complete.

### Indian market scope

  -----------------------------------------------------------------------
  Dataset                 Required coverage       Current status
  ----------------------- ----------------------- -----------------------
  NSE equities            Broad listed universe,  Pending comprehensive
                          historical OHLCV,       coverage
                          volume, delivery data   
                          where available         

  BSE equities            Broad listed universe   Pending
                          and historical OHLCV    

  Indian indices          Broad-market, sectoral, Partial: NIFTY 50,
                          thematic, volatility    NIFTY Bank, SENSEX
                          indices, NIFTY and BSE  ingested
                          families                

  Indian ETFs             Prices, NAV, holdings,  Pending
                          AUM, expense ratios     

  Indian mutual funds     NAV history, scheme     Pending
                          metadata, category,     
                          AUM, expense ratios,    
                          holdings where          
                          available               

  Indian company          Annual and quarterly    Pending comprehensive
  fundamentals            income statement,       coverage
                          balance sheet, cash     
                          flow, ratios, source    
                          and filing dates        

  Corporate actions       Dividends, splits,      Pending
                          bonuses, rights,        
                          buybacks, mergers,      
                          demergers, symbol       
                          changes                 

  Futures                 Index and stock         Pending
                          futures, expiry, OHLC,  
                          volume, open interest   

  Options                 Index and stock         Pending
                          options, expiry,        
                          strike, call/put, OHLC, 
                          volume, OI, IV and      
                          Greeks where available  

  Volatility              India VIX and related   Pending
                          data                    

  Bonds and fixed income  Government securities,  Pending
                          corporate bonds,        
                          yields, prices,         
                          coupons, maturities,    
                          yield curves            

  FX                      USD/INR, EUR/INR,       Pending
                          GBP/INR, JPY/INR and    
                          other available INR     
                          pairs                   

  Commodities             MCX and other reliable  Pending
                          Indian commodity-market 
                          data, including gold,   
                          silver, crude oil,      
                          natural gas             

  RBI and macroeconomics  Policy rates,           Pending
                          liquidity, inflation,   
                          GDP, IIP, employment,   
                          money/credit, balance   
                          of payments, fiscal     
                          data                    

  Economic calendar       RBI policy, inflation   Pending
                          and GDP releases,       
                          exchange holidays,      
                          earnings and other      
                          events                  
  -----------------------------------------------------------------------

### Recommended India implementation sequence

1.  Audit current Indian instruments and existing index coverage.
2.  Build a comprehensive NSE/BSE instrument master with exchange
    identifiers, ISINs, symbols, series, currency, listing status, and
    provider mappings.
3.  Select reliable data sources and document licensing and permitted
    use.
4.  Ingest NSE/BSE equity daily data and measure actual history by
    instrument.
5.  Expand Indian indices and ingest histories.
6.  Ingest Indian ETF and mutual-fund prices/NAV and available reference
    data.
7.  Ingest Indian company annual and quarterly financial statements.
8.  Ingest corporate actions and document adjustment methodology.
9.  Ingest futures, options, India VIX, and derivatives reference data.
10. Ingest government/corporate bonds, yields, and yield curves.
11. Ingest INR FX pairs and Indian commodity-market data.
12. Ingest RBI and other macroeconomic time series and the economic
    calendar.
13. Run completeness, reconciliation, duplicate, temporal, and S3
    audits.
14. Freeze and document the initial India data-lake release.

Use source-specific adapters behind a common ingestion interface. Record
source, retrieval time, effective date, units, currency, licensing
restrictions, and actual coverage. Do not assume one provider covers
every dataset.

### India completion criteria

-   Agreed NSE/BSE instrument universe is recorded and versioned.
-   Each dataset has a source, licensing note, schema, and documented
    coverage.
-   Ingestion is incremental and restartable.
-   Raw source files are stored in Bronze and verified in S3.
-   Silver datasets have consistent types, units, dates, and
    identifiers.
-   Corporate actions and adjustment methodology are documented.
-   Derivative contracts preserve expiry, strike, option type, and
    contract identifiers.
-   Macro series preserve observation date, release date/vintage where
    available, units, and frequency.
-   Coverage reports identify missing periods and unavailable
    instruments.
-   Registry, manifests, checksums, and data-quality tests pass.
-   Known limitations are documented rather than hidden.

## 4. SEC Company Facts and filing-level XBRL

### Company Facts ingestion

The company universe contains 153 equities across 10 countries. SEC CIK
mappings were created for 80 US companies. SEC Company Facts was
downloaded for all 80; raw JSON was stored in Bronze and uploaded to S3.

Manifest: `data/validation/fundamentals/sec_companyfacts_manifest.csv`\
Company universe:
`reference/universes/companies/global_company_universe.csv`

### Company Facts Silver outputs

Normalizer:
`ingestion/providers/fundamentals/sec_companyfacts_normalizer.py`

-   `data/silver/fundamentals/income_statement.parquet`
-   `data/silver/fundamentals/balance_sheet.parquet`
-   `data/silver/fundamentals/cash_flow_statement.parquet`
-   `data/silver/fundamentals/equity_statement.parquet`
-   `data/silver/fundamentals/supplementary_disclosures.parquet`
-   `data/silver/fundamentals/unclassified.parquet`

Last recorded counts:

  Dataset                        Observations
  --------------------------- ---------------
  Income statement                     95,541
  Balance sheet                        92,638
  Cash flow statement                  84,180
  Equity statement                      4,528
  Supplementary disclosures            61,495
  Unclassified                      1,881,101
  **Total**                     **2,219,483**

80/80 companies processed, zero normalization failures.

Limitations: - SEC Company Facts focuses on standard taxonomies and does
not preserve all custom filing taxonomy facts. - Company Facts can lose
filing-level dimensions and context distinctions needed for robust
statement reconciliation. - The 73 non-US companies need
country-specific or licensed fundamentals sources. - Initial concept
classification is not final. Continue improving exact mapping and
validation.

### Filing-level XBRL prototype

Downloaded source: `data/bronze/fundamentals/blk_20260630_xbrl.xml`

Extractor: `ingestion/providers/fundamentals/xbrl_filing_normalizer.py`

Silver output:
`data/silver/fundamentals/filing_facts/blk_20260630.parquet`

Example invocation:

``` bash
python ingestion/providers/fundamentals/xbrl_filing_normalizer.py \
  --source data/bronze/fundamentals/blk_20260630_xbrl.xml \
  --output data/silver/fundamentals/filing_facts/blk_20260630.parquet \
  --ticker BLK \
  --cik 0002012383 \
  --accession 0001193125-26-337177
```

Validated output: - 2,413 facts - 419 unique concepts - 751 unique
contexts - 1,735 dimension-bearing facts - One entity identifier - Zero
nil facts in this filing - Zero missing context IDs - Zero missing
namespaces

The extractor preserves concept, namespace, context ID, entity
identifier, period, dimensions, unit, raw value, numeric value, scale,
sign, decimals, nil status, and source file.

### Accounting reconciliation lesson from BLK

For 30 June 2026:

  Component                        USD
  ------------------ -----------------
  Assets               175,875,000,000
  Liabilities          111,335,000,000
  Temporary equity       6,700,000,000
  Permanent equity      57,840,000,000
  Calculated total     175,875,000,000
  Difference                         0

Temporary equity came from two separate dimension-bearing custom
taxonomy facts: - 3,872,000,000 under `srt:ConsolidatedEntitiesAxis` /
`blk:ConsolidatedSponsoredInvestmentProductsMember` - 2,828,000,000
under `us-gaap:StatementClassOfStockAxis` / `blk:SubCoMember`

The equation reconciles only when both are included.

Do not hardcode this BLK adjustment. Build a generalized filing-level
fact model that preserves custom concepts and dimensions. Avoid summing
consolidated totals and dimensional detail together. Reconciliation must
select appropriate statement contexts and account for temporary equity,
noncontrolling interests, and other presentation-specific components.

### SEC filing discovery

Scripts: -
`ingestion/providers/fundamentals/sec_filings/discover_filings.py` -
`ingestion/providers/fundamentals/sec_filings/collect_historical_filings.py`

Manifest: `data/validation/fundamentals/sec_filings_manifest.csv`

Current manifest: 3,027 records across 80 US companies.

  Form            Records
  ----------- -----------
  10-K                569
  10-Q              1,773
  20-F                 15
  6-K                 670
  **Total**     **3,027**

Discovery includes `10-K`, `10-Q`, `20-F`, `40-F`, and `6-K`. All 80
companies are represented.

Historical coverage is not complete. The SEC `filings.recent` list is
limited, and additional archives are referenced by `filings.files`.
Sample checks: - WFC: 9 archive files, including records back to 2011. -
AAPL: 1 archive file covering 1994--2015. - TSM: 1 archive file covering
1998--2014. - XOM: no separate archive files in the sample response.

**Known bug and restart action:** The first run of
`collect_historical_filings.py` added zero records. The parser was
patched to read historical archive JSON arrays from the top level, while
reading the main submissions JSON from `filings.recent`. The patched
collector has not yet been rerun and verified. Rerun it and confirm that
historical filing counts increase where archive records exist.

Historical archive JSON files are stored under:
`data/bronze/fundamentals/sec_submissions/`

After collection, audit coverage by ticker, form, and year. Do not
assume every 6-K contains structured financial statements. Track filing
availability and XBRL availability separately.

SEC User-Agent must use this exact ASCII email:

``` python
{"User-Agent": "FinancialDataLake/1.0 somdevsheel7@gmail.com"}
```

Avoid hidden Unicode characters in this header.

### Remaining fundamentals tasks

1.  Rerun and verify the patched historical SEC archive collector.
2.  Audit filing coverage per company and form.
3.  Identify filings with XBRL instance documents and taxonomy files.
4.  Download filing-level XBRL documents to immutable Bronze and S3.
5.  Record accession, CIK, ticker, form, filing date, report date,
    source URL, checksum, file size, and status.
6.  Normalize facts while preserving contexts, dimensions, custom
    namespaces, units, decimals, scale, sign, and nil flags.
7.  Partition Silver filing facts by company and filing/accession.
8.  Improve statement classification, including temporary equity,
    noncontrolling interests, and custom concepts.
9.  Build point-in-time statement selection and reconciliation tests.
10. Preserve unclassified facts.
11. Select country-specific or licensed providers for the 73 non-US
    companies.
12. Document coverage and licensing limitations.

## 5. Remaining global work after India

The following workstreams remain after Indian-market completion:

  ------------------------------------------------------------------------
                            \# Workstream            Status
  ---------------------------- --------------------- ---------------------
                             1 Complete historical   In progress; patched
                               SEC filing discovery  collector needs rerun
                                                     and audit

                             2 Bulk XBRL filing      Pending
                               download and S3       
                               storage               

                             3 Filing-level          Pending
                               normalization for all 
                               US companies          

                             4 Financial statement   Partial prototype;
                               classification and    broad implementation
                               reconciliation        pending

                             5 International company Pending
                               fundamentals (73      
                               companies)            

                             6 Corporate actions:    Pending
                               dividends, splits,    
                               mergers               

                             7 FX market data        Pending

                             8 Commodities and       Pending
                               futures               

                             9 Options chains and    Pending
                               implied volatility    

                            10 Bonds, yields and     Pending
                               interest-rate curves  

                            11 Macroeconomic data    Pending
                               and economic          
                               indicators            

                            12 Economic calendar     Pending

                            13 Crypto market data    Pending

                            14 Company profiles,     Pending
                               earnings and analyst  
                               estimates             

                            15 Silver/Gold           Pending
                               validation, data      
                               quality and coverage  
                               audits                

                            16 Final lake-wide       Pending
                               catalog, manifests    
                               and completeness      
                               audit                 
  ------------------------------------------------------------------------

The India work in Section 3 is an additional immediate priority and must
be completed before these global workstreams resume.

## 6. Planning estimates

Previously discussed estimates for remaining global work:

  Activity                                        Estimated effort
  -------------------------------------- -------------------------
  SEC historical XBRL pipeline                           3--5 days
  International fundamentals                             3--5 days
  Corporate actions and reference data                   2--3 days
  FX, commodities and futures                            3--4 days
  Options, bonds and rates                               4--6 days
  Macro, calendar and crypto                             3--4 days
  Earnings, profiles and analyst data                    2--4 days
  Final validation and catalog                           3--5 days
  **Total**                                **23--36 working days**

These are rough estimates for one developer, not guaranteed delivery
dates. They exclude the full Indian market completion effort, which must
be estimated after the Indian universe, source availability, and
required history are audited. Provider access, licensing, rate limits,
and historical data quality can change the schedule.

A previously discussed work cadence was approximately 4--5 focused hours
per day, with one lighter review day each week.

## 7. Restart checklist

### Step A: verify environment

-   [ ] Open `~/Project/Quantera`.
-   [ ] Activate `.venv`.
-   [ ] Check `.env` contains only non-secret configuration such as AWS
    region and bucket.
-   [ ] Verify AWS login/assumed-role access.
-   [ ] Verify S3 bucket `quant-global-data-lake-prod` in
    `ap-southeast-2`.
-   [ ] Check `git status` before changing files.

### Step B: resume Indian market phase

-   [ ] Audit the seven existing Indian equities and three existing
    Indian indices.
-   [ ] Inspect global equity and index universe CSV schemas.
-   [ ] Build and validate the NSE/BSE instrument universe.
-   [ ] Select approved data sources and document usage rights.
-   [ ] Implement India datasets through the existing Bronze/Silver/Gold
    and registry architecture.
-   [ ] Run per-dataset coverage and S3 audits.
-   [ ] Record gaps and provider limitations.
-   [ ] Mark India complete only after Section 3 completion criteria
    pass.

### Step C: resume SEC historical collection

-   [ ] Rerun
    `ingestion/providers/fundamentals/sec_filings/collect_historical_filings.py`.
-   [ ] Confirm archive records are added.
-   [ ] Audit unique accession counts and oldest filing date for all 80
    companies.
-   [ ] Check duplicates and missing metadata.
-   [ ] Separate forms that typically contain structured statements from
    disclosure-only forms.
-   [ ] Record XBRL document availability.

### Step D: continue global datasets

-   [ ] Complete filing-level XBRL downloads and normalization.
-   [ ] Complete international fundamentals.
-   [ ] Complete corporate actions, FX, commodities, futures, options,
    bonds, rates, macro, calendar, crypto, profiles, earnings, and
    estimates.
-   [ ] Perform full Silver/Gold validation and catalog generation.

### Step E: freeze data lake before API development

-   [ ] Verify every registered S3 object.
-   [ ] Verify manifests, checksums, schema versions, and source
    lineage.
-   [ ] Generate dataset-level coverage reports.
-   [ ] Generate final inventory of tables, schemas, partitions, and
    update cadence.
-   [ ] Document licensing and redistribution constraints.
-   [ ] Record known gaps and data-quality limitations.
-   [ ] Freeze and tag the initial data-lake release.
-   [ ] Only then design the data-access API.
-   [ ] Integrate the API into Quantera after API validation.

## 8. Operating rules

1.  Do not restart completed ingestion unnecessarily. Inspect manifests
    and registry first.
2.  Do not assume an initial universe is comprehensive. The 153-equity,
    20-ETF, and 25-index datasets are initial universes.
3.  Do not assume 20 years of data exist for every instrument. Measure
    and report actual coverage.
4.  Do not discard unclassified financial facts. Preserve them for
    future mapping.
5.  Do not sum XBRL dimensional facts blindly. Select the correct
    reporting context and avoid double counting.
6.  Do not treat all 6-K filings as standardized financial statements.
7.  Do not claim commercial rights based only on technical API access.
    Review provider licensing.
8.  Keep provider-specific logic behind adapters.
9.  Use immutable Bronze data and reproducible Silver transformations.
10. Do not begin Quantera API/application integration until the data
    lake has passed its completeness and quality audit.

## 9. Key project paths

``` text
reference/
  universes/
    global_equity_universe.csv
    companies/
      global_company_universe.csv
    etf/
    indices/

ingestion/
  providers/
    market_data_ingestion.py
    fundamentals/
      sec_companyfacts_normalizer.py
      xbrl_filing_normalizer.py
      sec_filings/
        discover_filings.py
        collect_historical_filings.py

registry/
  ingestion_registry.py
  instrument_master.py

data/
  bronze/
    fundamentals/
      sec_companyfacts/
      sec_submissions/
  silver/
    fundamentals/
      income_statement.parquet
      balance_sheet.parquet
      cash_flow_statement.parquet
      equity_statement.parquet
      supplementary_disclosures.parquet
      unclassified.parquet
      filing_facts/
  staging/
  quarantine/
  validation/
    fundamentals/
      sec_companyfacts_manifest.csv
      sec_filings_manifest.csv
```

Some directories and datasets will expand during the India phase.

## 10. First action when resuming

**Start with the Indian market audit, not SEC XML downloads.**

``` bash
python - <<'PY'
import pandas as pd

p = "reference/universes/global_equity_universe.csv"
d = pd.read_csv(p)

india = d[
    d.country.astype(str).str.upper().isin(["IN", "INDIA"])
]

print("Indian equity instruments:", len(india))
print(india.to_string(index=False))
PY
```

Use the result to establish the baseline before building the full
NSE/BSE universe.

**Sequencing reminder:** Complete the Indian market data lake first.
Then resume the remaining global datasets. Complete and audit the data
lake before building the API. Quantera integration comes after the API.

## 11. Status update — 9 November 2026

Details per dataset: `docs/india_data_lake.md`. Measured coverage and tests:
`data/validation/india/{coverage_report,quality_report}.csv`, lake catalog:
`data/validation/catalog/lake_catalog.md`. Rebuild all derived outputs and
audits with `scripts/finalize_lake.sh` (no S3 upload).

### India (Section 3) — done

| Dataset | Source | Coverage |
|---|---|---|
| NSE equity daily | NSE bhavcopy | 2000‑01 → 2026‑11‑06, 9.7M rows |
| BSE equity daily (all scrips) | BSE bhavcopy, registry-linked, quarantine, ledger | 2006‑03 → 2026‑11‑06, 16.7M rows |
| BSE delivery enrichment | BSE per-scrip API (existing pilot) | 1,000 scrips × 12 months |
| NSE indices (≈170 incl. India VIX, G‑Sec, BHARAT Bond) | NSE index file | 2012‑02 → 2026‑11 |
| NSE futures & options | NSE F&O bhavcopy | 2000‑06 → 2026‑11, 168M contract-days |
| Mutual funds / ETFs NAV | AMFI | 2006‑04 → 2026‑11, 37.8M rows, all schemes |
| Corporate actions + adjustment factors | NSE API | 2000 → 2026, 42,912 actions; factors validated (99% within ±25%) |
| Event calendar (results dates etc.) | NSE API | 2008 → Dec 2026, 124k events |
| INR FX, rates, macro | FRED | 24 series |
| Trading calendar + holidays | NSE + observed sessions | per dataset |
| Company fundamentals (XBRL) | NSE results API | **in progress** (see below) |

### SEC (Section 4) — done or in progress

* Historical collector rerun verified: 9,664 filings, 80/80 companies, 0 duplicates.
* Predecessor CIKs linked (XOM, BLK, GOOGL, DIS, LIN, AVGO): +461 filings;
  `reference/universes/companies/sec_predecessor_ciks.csv`.
* XBRL availability + form class recorded in the filings manifest.
* `xbrl_filing_normalizer.py` refactored to `extract()`; nil-flag bug fixed
  (namespaced `xsi:nil`; BLK has 5 nil facts, not 0).
* Bulk XBRL download + normalization: **in progress** (5,257 filings).
* `sec_statement_builder.py`: point-in-time statements + generalized
  balance-sheet reconciliation. BLK 2026‑06‑30 now reconciles to 0 without a
  hard-coded adjustment (temporary equity tested from dimensional candidates).

### Global (Section 5) — partly done

* Corporate actions for the 198 initial instruments (from existing Yahoo bars).
* FRED global: US Treasury curve, fed funds, SOFR, credit spreads, 16 FX
  series, WTI/Brent/Henry Hub, IMF metals & grains, BTC/ETH, VIX, US macro.

### Running when this was written (all restartable; rerun the same command)

```bash
python pipelines/india_nse_fo_backfill.py --start 2024-06-06 --end 2026-11-06
python pipelines/india_nse_financial_results.py --stage all
python ingestion/providers/fundamentals/sec_filings/download_xbrl_filings.py
scripts/after_fo_renormalize.sh   # rewrites F&O Silver to schema v2 after the F&O job
```
Then run `scripts/finalize_lake.sh`.

### Needs a decision / access

* S3 upload: parked, see §12.
* BSE delivery enrichment scope (which scrips × years to fetch via the per-scrip API).
* Not available from free sources: MCX commodities (site blocks automated
  access), G‑Sec/corporate bond prices and yield curves (CCIL/FBIL),
  non-US company fundamentals for the 73 non-US companies, analyst
  estimates, company profiles — need licensed providers.
* Done since (see §13): options IV/Greeks, US ETF holdings/AUM, release freeze tool,
  schema/cadence inventory, API design draft.
* Still open: ETF expense ratios; Indian ETF/MF AUM and TER (AMFI pages did not answer to
  automated requests); RBI/MOSPI release calendar (needs a source that permits automated access).

## 12. Storage change — 10 November 2026: local-only

The AWS account that held `quant-global-data-lake-prod` was closed and the
bucket's contents are gone. No data was lost: S3 only held copies.

* Verified locally: all 198 `ingestion_registry` objects exist at their
  `local_file` paths with matching `checksum_sha256`. All 80 SEC companyfacts
  files are present in Bronze.
* The lake (`data/`, ~29 GB) is the system of record until a new cloud target
  exists. The `s3_bucket` and `s3_key` columns in the registry are historical
  and no longer resolve.
* Second copy: `scripts/backup_lake_local.sh <dir> --execute`. This is an
  additive rsync to another disk followed by a checksum verification, and it
  logs to `data/validation/backup_log.txt`. Run it after `scripts/finalize_lake.sh`.
* Checklist items that require S3 (bucket access, `head-object` audits, "verified
  in S3") are suspended, not passed. When a new account exists, run
  `scripts/sync_india_to_s3.py` against the new bucket. Non-India data will need
  the same manifest-driven sync.
* Both copies are on the same machine. They protect against a failed disk, not
  against theft, fire or a ransomware attack on the machine. Keep an offline
  external drive for that.

## 13. Status update — 10 November 2026

| Item | Result |
|---|---|
| F&O Silver re-normalized (schema `nse_fo_v2`) | 6,518 days rebuilt from local Bronze |
| SEC filing-level XBRL | 5,251 filings downloaded and normalized, 8.95M facts (5 failed, 1 without instance) |
| NSE option IV + Greeks (Black-76) | `gold/india/derivatives/nse_option_greeks/`: 25.0M priced option-days 2001 → 2026; method in `docs/india_data_lake.md` §4a. NIFTY 30-day ATM IV tracks India VIX 0.5–1.3 vol points below it (VIX includes skew). |
| US ETF holdings + AUM (SEC N-PORT) | `pipelines/global_etf_nport.py`: 16 ETFs, 422 filings, 1.65M holdings, 2019-09 → 2026-08. The registry `etf_metadata` table is filled. AUM is set only for single-class series; Vanguard multi-class series report fund-level net assets (kept, not used as ETF AUM). SPY (UIT) and GLD/IAU/SLV (grantor trusts) file no N-PORT. |
| BSE API duplicate keys | 6 rows are a source quirk (500271, Jan 2000, two unlabeled rows per day). Kept and reported as a documented WARN. |
| India audit | 37 PASS / 0 FAIL / 2 WARN, 78,302 files |
| Inventory (Step E) | `data/validation/catalog/`: 70 datasets with schemas (`lake_schemas.csv`), schema version, lineage flag, update cadence and usage rights (`reference/dataset_cadence.csv`). Derived Gold tables have dataset-level lineage (pipeline + upstream), not per-row lineage. |
| Freeze | `scripts/freeze_release.py --tag <tag>` writes a checksummed release manifest; `--verify <tag>` detects drift. It refuses while jobs run or the audit fails. |
| API | `docs/api_design.md` (draft; implementation after the freeze). |

**Still running:** `pipelines/india_nse_financial_results.py --stage all`. Its expanded manifest
holds 180,915 filings, 91,815 with XBRL, across 2,040 companies, about one day at 1 request/s.
`scripts/resume_and_finalize.sh` then runs `finalize_lake.sh` and the local backup.
**Next:** `python scripts/freeze_release.py --tag lake-2026.11`.

## 14. Status update — 12 November 2026

| Item | Result |
|---|---|
| NSE financial results | 72,857 XBRL filings normalized, 0 failed. 295 documents are unavailable on NSE. Key financials Gold: 72,857 rows after deduplicating repeated XBRL documents. Duplicate facts are flagged (`is_duplicate_fact`), not dropped. Facts with undefined contexts and no period are `UNRESOLVED_CONTEXT`. |
| SEC statements | 5,251 filings: 5,135 BALANCED, 5 BALANCED_DIMENSIONAL_TEMP_EQUITY, 67 UNBALANCED, 44 INCOMPLETE (mostly 20-F/6-K) |
| India audit | 37 PASS / 0 FAIL / 2 WARN, 215,242 files, 16.41 GB |
| Local backup | Complete. The checksum compare against `data/` showed 0 files differing (11 Nov, 19:53 UTC). |
| **Release frozen (Step E)** | `lake-2026.11` (frozen 11 Nov 2026, 20:15 UTC): 234,798 files, 39.5 GB. `--verify` reports 0 missing / 0 changed / 0 added. |
| **API v1 (Step E → F)** | Implemented in `api/`. `pytest tests/api`: 35 passed, covering contract, point-in-time, known answers, drift, licensing and errors. All load-test p95 targets met. See `docs/api_design.md` §10. |

The API surfaced these lake findings, which are handled in the API and should be fixed in the next release:
- Yahoo Silver prices are split-adjusted, despite the label `UNADJUSTED_OHLC_WITH_ADJ_CLOSE`.
- NSE equity rows before about 2011 have no ISIN, so they have no `instrument_id`.
- NSE `is_latest_filing` is computed with look-ahead.
- Only 7 global symbols are in Silver.

**Next:** Quantera integration (Step F), reading only through the API. Pending owner decisions are listed in `docs/api_design.md` §9.

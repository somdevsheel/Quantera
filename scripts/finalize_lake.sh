#!/usr/bin/env bash
# Rebuild derived outputs and audits after the long-running downloads finish.
# Safe to rerun. Storage is local-only (S3 account closed Nov 2026); back up with
# scripts/backup_lake_local.sh <dir> --execute after this finishes.
#
# Long-running jobs this depends on (check with: pgrep -af "pipelines/|download_xbrl"):
#   pipelines/india_nse_fo_backfill.py
#   pipelines/india_nse_financial_results.py --stage all
#   ingestion/providers/fundamentals/sec_filings/download_xbrl_filings.py
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate

if pgrep -f "^python .*(india_nse_fo_backfill|india_nse_financial_results|download_xbrl_filings)\.py" >/dev/null; then
  echo "WARNING: downloads still running; outputs will be partial." >&2
fi

step() { echo; echo "=== $* ==="; }

step "India: NSE fundamentals normalize (idempotent)"
python pipelines/india_nse_financial_results.py --stage normalize --reparse
step "India: corporate actions rebuild"
python pipelines/india_nse_corporate_actions.py --build-only
step "India: option implied vol + Greeks (incremental)"
if pgrep -f "^python .*india_nse_option_greeks\.py" >/dev/null; then echo "already running; skipped"; else python pipelines/india_nse_option_greeks.py; fi
step "India: trading calendar"
python pipelines/india_trading_calendar.py
step "India: BSE bhavcopy coverage ledger"
python pipelines/india_bse_bhavcopy_coverage_ledger.py
step "India: audit (manifest + checksums + coverage + quality)"
python validation/india_lake_audit.py
step "SEC: normalize any downloaded XBRL not yet parsed"
python ingestion/providers/fundamentals/sec_filings/download_xbrl_filings.py --stage normalize
step "SEC: point-in-time statements + reconciliation"
python ingestion/providers/fundamentals/sec_statement_builder.py
step "Global: ETF holdings (N-PORT) normalize"
python pipelines/global_etf_nport.py --stage normalize
step "Global: corporate actions"
python pipelines/global_corporate_actions.py
step "Lake-wide catalog"
python validation/lake_catalog.py
echo; echo "Done. Reports: data/validation/india/, data/validation/fundamentals/, data/validation/catalog/"

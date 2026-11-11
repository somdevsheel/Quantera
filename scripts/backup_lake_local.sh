#!/usr/bin/env bash
# Mirror the lake (data/) to a second local disk. Replaces the S3 sync while
# storage is local-only. Default is a dry run; pass --execute to copy.
#
#   scripts/backup_lake_local.sh "/media/somdevsheel/New Volume11/quantera_backup"            # preview
#   scripts/backup_lake_local.sh "/media/somdevsheel/New Volume11/quantera_backup" --execute  # copy + verify
#
# Additive only: files deleted locally are NOT deleted from the backup, so a
# mistake in data/ can be recovered. Bronze is immutable anyway.
# Works on NTFS/exFAT targets (no permission/owner preservation).
set -euo pipefail
cd "$(dirname "$0")/.."

DEST="${1:?usage: $0 <backup_dir> [--execute]}"
MODE="${2:-}"
mkdir -p "$DEST"

need=$(du -sb data | cut -f1)
free=$(df -B1 --output=avail "$DEST" | tail -1)
echo "lake: $((need / 1000000000)) GB | free at destination: $((free / 1000000000)) GB"

OPTS=(-rt --modify-window=2 --no-perms --no-owner --no-group --exclude='*.tmp' --exclude='*.parquet.tmp')
if [[ "$MODE" != "--execute" ]]; then
  rsync "${OPTS[@]}" --dry-run --stats data/ "$DEST/data/" | tail -15
  echo; echo "Dry run only. Re-run with --execute to copy."
  exit 0
fi

if [[ -t 1 ]]; then PROGRESS=(--info=progress2); else PROGRESS=(--stats); fi  # no progress spam in logs
rsync "${OPTS[@]}" "${PROGRESS[@]}" data/ "$DEST/data/"
cp -f data/registry/quantera_registry.db "$DEST/data/registry/" 2>/dev/null || true

echo; echo "Verifying content (checksum compare, may take a while)..."
diffs=$(rsync "${OPTS[@]}" --checksum --dry-run --itemize-changes data/ "$DEST/data/" | grep -c '^>f' || true)
date -u +"%Y-%m-%dT%H:%M:%SZ backup to $DEST: $diffs files differ" | tee -a data/validation/backup_log.txt
[[ "$diffs" == 0 ]] && echo "OK: backup matches data/" || { echo "WARNING: $diffs files differ (jobs still writing?) - rerun"; exit 1; }

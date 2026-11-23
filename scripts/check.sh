#!/usr/bin/env bash
# Full local quality gate for the Quantera platform (needs the local lake and PostgreSQL).
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
PY=.venv/bin
$PY/ruff check quantera quant migrations tests/platform tests/quant
$PY/mypy
$PY/alembic upgrade head
$PY/alembic check
$PY/pytest -q tests "$@"

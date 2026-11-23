#!/usr/bin/env bash
# Start a platform API for end-to-end tests on its own schema ("e2e"), so test accounts never
# touch real data. Usage: scripts/e2e_api.sh [port]   (default 8103; the lake API must be up)
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
export QUANTERA_DB_SCHEMA=e2e QUANTERA_ENVIRONMENT=test QUANTERA_AUTH_RATE_LIMIT_PER_MINUTE=1000
.venv/bin/python - <<'PY'
from sqlalchemy import create_engine, text
from quantera.core.config import get_settings
s = get_settings()
with create_engine(s.database_url).begin() as c:
    c.execute(text('DROP SCHEMA IF EXISTS "e2e" CASCADE'))
    c.execute(text('CREATE SCHEMA "e2e"'))
PY
.venv/bin/alembic upgrade head >/dev/null
exec env QUANTERA_API_PORT="${1:-8103}" .venv/bin/python -m quantera.api

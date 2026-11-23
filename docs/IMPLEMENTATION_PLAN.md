# Quantera platform — implementation plan

**Scope:**
- `claude_code_master_prompt_quant_platform_price_levels_only.docx`: the master prompt;
- `Quant_Stock_Platform_PRD_System_Design (1).docx`: the PRD.

Together they specify one product: web + Android clients, a FastAPI backend, PostgreSQL for
operational state, the Parquet/DuckDB data lake, and background workers. It is built **in this
repository**, on top of the existing lake.

## What already exists

- **Data lake:** pipelines (`pipelines/`); raw, cleaned and research-ready Parquet layers
  (`data/`); validation and audit (`validation/`); frozen, checksummed releases
  (`lake-2026.12`).
- **Read-only lake API:** `api/`, on port 8001; see `docs/api_design.md` and
  `docs/india_data_lake.md`.

## Layout added

| Path | Contents |
|---|---|
| `quantera/` | Platform backend (Python package) |
| `quantera/core/` | Settings, database, security, errors, audit |
| `quantera/identity/` | Users, workspaces, memberships, sessions |
| `quantera/api/` | FastAPI app factory and `/api/v1` routes. Run with `python -m quantera.api` on port **8002**. |
| `quant/` | Pure, tested calculation packages: indicators, volatility, strategies, forecasting, portfolio, risk, execution simulation. These have no database or HTTP code. |
| `migrations/` | Alembic migrations for the platform database |
| `apps/web/` | Next.js + TypeScript web app (port 3000) |
| `apps/android/` | React Native + Expo Android app |
| `tests/platform/` | Platform tests, run against real PostgreSQL (a throwaway schema per run) |

## Decisions

| Topic | Decision |
|---|---|
| Operational database | PostgreSQL 14 on this machine (port 5433), database `quantera`. The URL is in `.env` as `QUANTERA_DATABASE_URL`; that file is gitignored with owner-only permissions. |
| Analytical data | Read from the frozen lake release. Never write operational state into the lake. |
| Queue and cache | Redis (port 6379), used for background jobs. Long scans and training return `202 Accepted` plus a job ID. |
| Authentication | Passwords hashed with Argon2id. Short-lived JWT access tokens are tied to a server-side session. Refresh tokens rotate, and reusing an old one revokes all of that user's sessions. Optional MFA. |
| Authorization | Workspace roles: owner, member, read-only guest, operator/admin. Every resource is checked in the backend. Everything is private by default; sharing is explicit, scoped and revocable. |
| ML | Training uses the CPU first. The RTX 3050 (6 GB) is used only when deep learning beats the simple baselines. MLflow serves as the model registry. Promotion is never automatic. |
| Execution | Paper trading only. No live broker path exists. |

## Delivery stages (PRD §12, master prompt §18)

| # | Stage | Status |
|---|---|---|
| 1 | Foundation: settings, database, migrations, identity, API conventions, health checks, tests | **Done.** Settings, migrations (round-trip tested), sign-in, rotating sessions, workspaces, audit; TOTP MFA with single-use recovery codes and replay protection (secrets Fernet-encrypted at rest); password change and reset (no account enumeration; ends all sessions; needs the second factor when MFA is on). CI (`.github/workflows/ci.yml`) runs lint, types, migrations and calculation tests; `scripts/check.sh` runs the full gate locally, including the lake-backed tests. No mail service yet: reset and invite tokens are returned to the caller outside production. |
| 2 | Universes (index, sector, watchlist, CSV, holdings) and point-in-time snapshots from the lake | **Done**, except holdings universes, which come with portfolios in stage 5. The lake has one index/sector snapshot (2026-11-12), so historical membership is refused rather than back-filled. |
| 3 | Batch scanner jobs, features, cross-sectional ranking, decision records | **Done.** PostgreSQL job queue (202 plus job ID, progress, cancel, retry, idempotency, quotas), versioned decision policy, results with filters, cursor paging and formula-safe CSV export. |
| 4 | Stock research workspace (`/stocks/{exchange}/{symbol}`) | **Done.** Sections: overview, technicals (13 indicators), volatility (5 estimators), statistics, fundamentals, valuation, events, risk. Forecasts arrive in stage 8; options are not supported. |
| 5 | Portfolio, holdings, risk engine, holding states | **Done.** Append-only ledger; splits and bonuses applied on their ex-date; CSV import; risk (VaR/ES, contributions, stress tests, liquidity); holding review scans. |
| 6 | Market-neutral builder; stop-loss, target and trailing-stop trade plans | **Done.** Optimizer with infeasibility diagnosis and lot rounding; index-hedged and low-beta modes; out-of-sample simulation; paper-only approval. Trade plans: 4-field price panel, linked OCO exits, confirmed risk increases, idempotent evaluation. |
| 7 | Strategy library, backtests, walk-forward validation | **Done.** 20 families with honest statuses, 7 signals, point-in-time liquid universe (delisted stocks included), itemised Indian costs, walk-forward, deflated Sharpe counting every trial. |
| 8 | ML lifecycle, registry, monitoring, rollback | **Done.** Excess-return labels used only once mature; purged, embargoed walk-forward; ZERO/MOMENTUM baselines vs RIDGE, LOGIT, GBM; promotion gates (data, leakage, folds, performance, stability, net of cost, calibration); four-eyes approval; checksummed artifacts; PSI drift and realised-outcome monitoring; rollback. Forecasts shown on the research page only from an approved model. |
| 9 | Paper trading, alerts, collaboration and sharing | **Done.** Paper orders fill on the next session (participation cap, DAY/GTC, pre-trade checks, RISK_OFF policy, reconciliation into the ledger). Alerts on the latest daily bar with dedup and cooldown. Invite-only group workspaces (single-use, 7-day, email-bound invites; GUEST is read-only), workspace switching, and read-only, expiring, revocable shares of watchlists, scans and backtests. No email service is configured, so invite tokens are returned once to the inviter. |
| 10 | Web and Android workflows | Planned |
| 11 | Advanced gated strategies and derivatives | Planned |
| 12 | Security hardening, load tests, backups, restore drills | Planned |

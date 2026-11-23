"""Batch scan: universe snapshot -> point-in-time data from the lake -> features -> ranking ->
decision records. Runs in a worker; never inside an HTTP request.

Point in time
-------------
* Prices: bars on or before ``as_of``; adjustments use only corporate actions known by then.
* Fundamentals: results filed on or before ``as_of`` (the lake filters by filing time).
* Membership: an index/sector snapshot dated after ``as_of`` is refused at enqueue (future
  membership would leak into the past).
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from quant import features as fx
from quant import portfolio as pq
from quant import scoring as sc
from quantera.analysis.jobs import Cancelled, Progress
from quantera.analysis.models import AnalysisJob, DecisionRecord
from quantera.lake.client import LakeClient
from quantera.universes.models import UniverseSnapshot

BENCHMARK = "NSEIDX:NIFTY_50"
PRICE_LOOKBACK_DAYS = 560  # covers 253 trading days plus holidays
HORIZON = "20d"
REVIEW_AFTER_DAYS = 7


def _clean(v: Any) -> Any:
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    return v


def latest_market_date(lake: LakeClient, on_or_before: date) -> date:
    rows = lake.index_daily(BENCHMARK, on_or_before - timedelta(days=20), on_or_before)
    if not rows:
        raise ValueError(f"no market data on or before {on_or_before}")
    return date.fromisoformat(str(rows[-1]["trade_date"])[:10])


def run(sessions: Callable[[], Session], lake: LakeClient, job_id: uuid.UUID) -> None:
    with sessions() as s:
        job = s.get(AnalysisJob, job_id)
        assert job is not None and job.as_of is not None and job.universe_snapshot_id
        snap = s.get(UniverseSnapshot, job.universe_snapshot_id)
        assert snap is not None
        members = list(snap.members)
        as_of, params = job.as_of, dict(job.params)
        versions = {"policy": sc.POLICY_VERSION, "features": fx.FEATURE_VERSION,
                    "universe_snapshot": str(snap.id), "universe_hash": snap.content_hash,
                    "lake_release": snap.lake_release}
        workspace_id = job.workspace_id
    policy = sc.Policy(**{k: v for k, v in params.get("policy", {}).items()
                          if k in sc.Policy.__dataclass_fields__ and k != "version"})
    progress = Progress(sessions, job_id)
    bench_rows = lake.index_daily(BENCHMARK, as_of - timedelta(days=PRICE_LOOKBACK_DAYS), as_of)
    bench = {date.fromisoformat(str(r["trade_date"])[:10]): float(r["close"]) for r in bench_rows}
    rows: list[dict[str, Any]] = []
    meta: dict[str, dict[str, Any]] = {}
    failed = 0
    release_seen: set[str] = set()
    for i, m in enumerate(members, 1):
        iid = m["instrument_id"]
        try:
            pm, prices = lake.prices_daily(instrument_id=iid, exchange=m.get("exchange"),
                                           start=as_of - timedelta(days=PRICE_LOOKBACK_DAYS),
                                           end=as_of, adjust="total", as_of=as_of)
            release_seen.add(str(pm.get("release")))
            b = fx.Bars.from_rows(prices)
            feats = fx.price_features(b, bench) if len(b) else {}
            last_close = float(b.close[-1]) if len(b) else None
            stmts = lake.nse_statements(m["symbol"], as_of) if m.get("exchange") == "XNSE" else []
            ff, prov = fx.fundamental_features(stmts, last_close, as_of)
            feats.update(ff)
            fund_end = date.fromisoformat(prov["latest_period_end"]) if prov[
                "latest_period_end"] else None
            rows.append({"instrument_id": iid, "symbol": m["symbol"], "features": feats,
                         "price_as_of": b.dates[-1] if len(b) else None,
                         "fundamentals_period_end": fund_end})
            meta[iid] = {"prices_last": b.dates[-1].isoformat() if len(b) else None,
                         "price_rows": len(b), "fundamentals": prov}
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not sink the batch
            failed += 1
            rows.append({"instrument_id": iid, "symbol": m["symbol"],
                         "failed": f"{type(exc).__name__}: {str(exc)[:160]}"})
            meta[iid] = {}
        if (i % 10 == 0 or i == len(members)) and not progress.update(i, len(members), failed):
            raise Cancelled
    decisions = sc.score(rows, as_of=as_of, policy=policy)
    by_id = {m["instrument_id"]: m for m in members}
    weights: dict[str, float] = params.get("weights", {})
    max_w = float(params.get("max_position_weight", 0.10))
    feats_by_id = {r["instrument_id"]: r.get("features") or {} for r in rows}
    counts: dict[str, int] = {}
    coverage: dict[str, int] = {}
    if len(release_seen - {"None"}) > 1:
        raise RuntimeError(f"lake release changed during the scan: {sorted(release_seen)}")
    with sessions() as s:
        for rank, d in enumerate(decisions, 1):
            m = by_id[d.instrument_id]
            counts[d.state] = counts.get(d.state, 0) + 1
            coverage[d.coverage] = coverage.get(d.coverage, 0) + 1
            s.add(DecisionRecord(
                workspace_id=workspace_id, job_id=job_id, instrument_id=d.instrument_id,
                symbol=d.symbol, exchange=m.get("exchange") or "XNSE", name=m.get("name"),
                industry=m.get("industry"), as_of=as_of, horizon=HORIZON,
                review_by=as_of + timedelta(days=REVIEW_AFTER_DAYS), state=d.state,
                holding_state=(pq.holding_state(d.state, d.pillars.get("RISK"),
                                                weights[d.instrument_id], max_w)[0]
                               if d.instrument_id in weights else None),
                coverage=d.coverage, rank=rank, opportunity=d.opportunity,
                uncertainty=d.uncertainty, pillars=_clean(d.pillars),
                features=_clean(feats_by_id.get(d.instrument_id, {})),
                supporting=d.supporting, counter=d.counter, missing=d.missing,
                overrides=d.overrides, explanation=d.explanation[:1000],
                data_as_of=meta.get(d.instrument_id, {}), versions=versions))
        job2 = s.get(AnalysisJob, job_id)
        assert job2 is not None
        job2.summary = {"states": counts, "coverage": coverage, "members": len(members),
                        "benchmark": BENCHMARK, "policy": params.get("policy", {}),
                        "notes": params.get("notes", [])}
        job2.versions = versions
        job2.status = "PARTIAL" if failed else "SUCCEEDED"
        job2.failed_items = failed
        s.commit()

"""/api/v1/portfolio-construction: market-neutral / low-beta builder (PRD 13)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any

import numpy as np
from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from quant import stats
from quantera.analysis import jobs
from quantera.analysis.scan import BENCHMARK, latest_market_date
from quantera.api.deps import PrincipalDep, SessionDep, WriterDep, request_id
from quantera.api.routes.analysis import _job
from quantera.api.routes.universes import LakeDep
from quantera.construction import service as cs
from quantera.construction.models import ProposalApproval
from quantera.core import audit
from quantera.core.errors import Conflict, DomainError
from quantera.portfolios import service as psvc
from quantera.universes import service as usvc

router = APIRouter(prefix="/portfolio-construction", tags=["portfolio construction"])


class ApproveIn(BaseModel):
    confirm: bool = False
    note: str = Field(min_length=3, max_length=500)
    paper_portfolio_id: uuid.UUID | None = None


def _proposal(session: Any, p: Any, job_id: uuid.UUID) -> tuple[Any, dict[str, Any]]:
    job = jobs.get(session, p, job_id)
    if job.kind != "OPTIMIZE":
        raise DomainError("Not a portfolio-construction job")
    if job.status != "SUCCEEDED":
        raise Conflict(f"The job is {job.status}; no proposal yet")
    prop: dict[str, Any] = job.summary.get("proposal", {})
    return job, prop


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
def create_job(body: cs.ConstructionIn, request: Request, p: WriterDep, session: SessionDep,
               lake: LakeDep,
               idempotency_key: Annotated[str | None, Header(max_length=80)] = None
               ) -> JSONResponse:
    if (body.universe_snapshot_id is None) == (body.universe_id is None):
        raise DomainError("give exactly one of universe_snapshot_id or universe_id")
    if body.universe_id is not None:
        snap = usvc.resolve(session, p, lake, body.universe_id, usvc.SnapshotIn(),
                            request_id(request))
    else:
        assert body.universe_snapshot_id is not None
        snap = usvc.get_snapshot(session, p, body.universe_snapshot_id)
    if snap.member_count > 300:
        raise DomainError("Portfolio construction is limited to 300 names; narrow the universe")
    as_of = body.as_of or latest_market_date(lake, date.today())
    params = {"request": body.model_dump(mode="json") | {"universe_snapshot_id": str(snap.id),
                                                         "universe_id": None}}
    job, created = jobs.enqueue(session, p, "OPTIMIZE", params, idempotency_key=idempotency_key,
                                request_id=request_id(request), universe_snapshot_id=snap.id,
                                as_of=as_of, total_items=snap.member_count)
    out = _job(job).model_dump(mode="json")
    out["status_url"] = f"/api/v1/portfolio-construction/jobs/{job.id}"
    return JSONResponse(out, status_code=202 if created else 200,
                        headers={"Location": out["status_url"]})


@router.get("/jobs/{job_id}")
def get_job(job_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> dict[str, Any]:
    job = jobs.get(session, p, job_id)
    out = _job(job).model_dump(mode="json")
    appr = session.query(ProposalApproval).filter_by(job_id=job.id).one_or_none()
    out["approval"] = ({"status": appr.status, "note": appr.note,
                        "decided_at": appr.decided_at.isoformat()} if appr else None)
    return out


@router.post("/{job_id}/simulate")
def simulate(job_id: uuid.UUID, p: PrincipalDep, session: SessionDep, lake: LakeDep,
             end: date | None = None) -> dict[str, Any]:
    """Hold the rounded proposal (plus any index hedge) FORWARD from its as_of date. This is
    out of sample only when as_of is in the past; with no later data it says so."""
    job, prop = _proposal(session, p, job_id)
    legs = prop.get("legs") or []
    if not legs:
        raise DomainError("The proposal has no legs to simulate")
    start = job.as_of
    assert start is not None
    end = end or latest_market_date(lake, date.today())
    if end <= start:
        return {"status": "NO DATA AFTER AS_OF",
                "note": f"the proposal is dated {start} and the lake ends {end}: build it with an "
                        "earlier as_of to see out-of-sample behaviour"}
    curves = []
    for leg in legs:
        _, rows = lake.prices_daily(instrument_id=leg["instrument_id"], exchange=leg["exchange"],
                                    start=start, end=end, adjust="total", as_of=end)
        curves.append({date.fromisoformat(str(r["trade_date"])[:10]): float(r["close"])
                       for r in rows})
    bench = {date.fromisoformat(str(r["trade_date"])[:10]): float(r["close"])
             for r in lake.index_daily(BENCHMARK, start, end)}
    days = sorted(set(bench).intersection(*[set(c) for c in curves]))
    if len(days) < 2:
        return {"status": "NO DATA AFTER AS_OF", "note": "fewer than two common trading days"}
    w = np.array([leg["weight"] for leg in legs])
    rel = np.array([[c[d] / c[days[0]] - 1 for c in curves] for d in days])
    pnl = rel @ w
    hedge = prop.get("hedge")
    brel = np.array([bench[d] / bench[days[0]] - 1 for d in days])
    if hedge:
        pnl = pnl + hedge["weight"] * brel
    nav = 1 + pnl
    summ = stats.summary(nav, 1 + brel, min_obs=5)
    return {"status": "OK", "out_of_sample": True, "start": days[0].isoformat(),
            "end": days[-1].isoformat(), "total_return": float(pnl[-1]),
            "benchmark_return": float(brel[-1]), "max_drawdown": summ.get("max_drawdown"),
            "realised_beta": summ.get("beta"), "volatility": summ.get("volatility"),
            "curve": [{"date": d.isoformat(), "nav": float(v)} for d, v in zip(days, nav,
                                                                                strict=True)],
            "assumptions": ["weights held constant (no rebalancing, no stops)",
                            "total-return prices; costs, borrow fees and financing not deducted"]}


@router.post("/{job_id}/approve-paper")
def approve_paper(job_id: uuid.UUID, body: ApproveIn, request: Request, p: WriterDep,
                  session: SessionDep, lake: LakeDep) -> dict[str, Any]:
    """Explicit approval for PAPER trading only. Long-only books (incl. the long side of an
    index-hedged book) can be booked into a paper portfolio; short legs cannot be booked in a
    cash paper book (no SLB model), so long/short proposals are approved for simulation only."""
    job, prop = _proposal(session, p, job_id)
    if not body.confirm:
        raise Conflict("Approval must be explicit: send confirm=true")
    if prop.get("status") not in ("OPTIMAL", "APPROXIMATE") or not prop.get("all_checks_pass"):
        raise Conflict("Only a feasible proposal whose rounded book passes every check can be "
                       "approved")
    if session.query(ProposalApproval).filter_by(job_id=job.id).one_or_none():
        raise Conflict("This proposal was already decided")
    legs = prop.get("legs", [])
    booked: list[str] = []
    if body.paper_portfolio_id:
        pf = psvc.get(session, p, body.paper_portfolio_id)
        if not pf.is_paper:
            raise DomainError("Proposals are booked only into PAPER portfolios")
        if any(leg["side"] == "SHORT" for leg in legs):
            raise DomainError("Short legs cannot be booked in a cash paper portfolio (no "
                              "securities-lending model); approve without a portfolio to "
                              "track it by simulation")
        txs = psvc.add_transactions(session, p, lake, pf, [
            psvc.TxIn(trade_date=job.as_of, type="BUY", symbol=leg["symbol"],
                      exchange=leg["exchange"] or "NSE", quantity=leg["shares"],
                      price=leg["price"], external_ref=f"proposal:{job.id}:{leg['symbol']}")
            for leg in legs], source="proposal", request_id=request_id(request))
        booked = [t.symbol or "" for t in txs]
    appr = ProposalApproval(workspace_id=p.workspace_id, job_id=job.id, status="APPROVED_PAPER",
                            decided_by=p.user_id, decided_at=datetime.now(UTC), note=body.note,
                            paper_portfolio_id=body.paper_portfolio_id)
    session.add(appr)
    audit.record(session, "proposal.approve_paper", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="analysis_job", resource_id=job.id, request_id=request_id(request),
                 details={"booked": booked, "label": prop.get("label")})
    return {"status": "APPROVED_PAPER", "booked": booked, "live_trading": "disabled",
            "note": "approval covers paper trading only"}


"""/api/v1/strategies (library) and /api/v1/backtests (jobs + experiment registry)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from quant import strategies as stg
from quantera.analysis import jobs
from quantera.analysis.models import AnalysisJob
from quantera.api.deps import PrincipalDep, SessionDep, WriterDep, request_id
from quantera.api.routes.analysis import _job
from quantera.core.errors import NotFound
from quantera.research.backtests import BacktestIn
from quantera.universes import service as usvc

router = APIRouter(tags=["strategies and backtests"])


@router.get("/strategies")
def library(p: PrincipalDep) -> dict[str, Any]:
    fams = stg.library()
    counts: dict[str, int] = {}
    for f in fams:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    return {"families": fams, "counts": counts, "statuses": list(stg.STATUSES),
            "signals": sorted(stg.SIGNALS),
            "note": "Nothing is above RESEARCH by default; promotion needs recorded evidence. "
                    "DATA_BLOCKED families list what is missing in blocked_by."}


@router.get("/strategies/{code}")
def family(code: str, p: PrincipalDep) -> dict[str, Any]:
    for f in stg.library():
        if f["code"] == code.upper():
            return f
    raise NotFound(f"Unknown strategy family {code}")


@router.post("/backtests", status_code=status.HTTP_202_ACCEPTED)
def create(body: BacktestIn, request: Request, p: WriterDep, session: SessionDep,
           idempotency_key: Annotated[str | None, Header(max_length=80)] = None) -> JSONResponse:
    snap_id = None
    if body.universe_snapshot_id:
        snap_id = usvc.get_snapshot(session, p, body.universe_snapshot_id).id
    job, created = jobs.enqueue(session, p, "BACKTEST",
                                {"request": body.model_dump(mode="json")},
                                idempotency_key=idempotency_key, request_id=request_id(request),
                                universe_snapshot_id=snap_id, as_of=body.end)
    job.summary = {"family": stg.family_of(body.signal)}
    out = _job(job).model_dump(mode="json")
    out["status_url"] = f"/api/v1/backtests/{job.id}"
    return JSONResponse(out, status_code=202 if created else 200,
                        headers={"Location": out["status_url"]})


@router.get("/backtests")
def registry(p: PrincipalDep, session: SessionDep,
             limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[dict[str, Any]]:
    """Every backtest ever attempted in the workspace, failed ones included (multiple-testing
    record)."""
    rows = session.scalars(select(AnalysisJob).where(
        AnalysisJob.workspace_id == p.workspace_id, AnalysisJob.kind == "BACKTEST")
        .order_by(AnalysisJob.created_at.desc()).limit(limit))
    out = []
    for j in rows:
        res = j.summary.get("result", {})
        out.append({"id": str(j.id), "status": j.status, "created_at": j.created_at.isoformat(),
                    "family": j.summary.get("family"), "signal": j.params["request"]["signal"],
                    "start": j.params["request"]["start"], "end": j.params["request"]["end"],
                    "metrics": {k: res.get("metrics", {}).get(k) for k in
                                ("cagr", "sharpe", "max_drawdown")},
                    "deflated_sharpe": j.summary.get("deflated_sharpe", {}).get(
                        "deflated_sharpe_probability"), "error": j.error})
    return out


@router.get("/backtests/{job_id}")
def get(job_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> dict[str, Any]:
    j = jobs.get(session, p, job_id)
    if j.kind != "BACKTEST":
        raise NotFound("Backtest not found")
    return _job(j).model_dump(mode="json")

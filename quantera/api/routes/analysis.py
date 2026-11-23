"""/api/v1/analysis: batch scan jobs (202 + job id), status, cancel, retry, results, export."""

from __future__ import annotations

import base64
import csv
import io
import json
import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quantera.analysis import jobs
from quantera.analysis.models import AnalysisJob, DecisionRecord
from quantera.analysis.scan import latest_market_date
from quantera.api.deps import PrincipalDep, SessionDep, WriterDep, request_id
from quantera.api.routes.universes import LakeDep
from quantera.core.errors import DomainError, NotFound
from quantera.identity.service import Principal
from quantera.lake.client import LakeClient
from quantera.universes import service as usvc
from quantera.universes.models import UniverseSnapshot

router = APIRouter(prefix="/analysis", tags=["analysis"])
MEMBERSHIP_TOLERANCE_DAYS = 31
STATES = ("BUY CANDIDATE", "WAIT", "AVOID", "NO TRADE", "INSUFFICIENT DATA", "STALE DATA")


class PolicyIn(BaseModel):
    min_adv_value: float | None = Field(default=None, ge=0)
    max_fundamental_age_days: int | None = Field(default=None, ge=30, le=3650)
    max_price_age_days: int | None = Field(default=None, ge=1, le=60)
    buy_opportunity: float | None = Field(default=None, ge=50, le=100)


class ScanIn(BaseModel):
    universe_snapshot_id: uuid.UUID | None = None
    universe_id: uuid.UUID | None = None  # resolved to a fresh snapshot now
    as_of: date | None = None  # default: latest market date in the lake
    policy: PolicyIn = Field(default_factory=PolicyIn)

    @model_validator(mode="after")
    def _one(self) -> ScanIn:
        if (self.universe_snapshot_id is None) == (self.universe_id is None):
            raise ValueError("give exactly one of universe_snapshot_id or universe_id")
        return self


class JobOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    created_at: datetime
    as_of: date | None
    universe_snapshot_id: uuid.UUID | None
    progress: float
    total_items: int
    done_items: int
    failed_items: int
    summary: dict[str, Any]
    versions: dict[str, Any]
    params: dict[str, Any]
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    status_url: str
    results_url: str


def _job(j: AnalysisJob) -> JobOut:
    return JobOut(id=j.id, kind=j.kind, status=j.status, created_at=j.created_at, as_of=j.as_of,
                  universe_snapshot_id=j.universe_snapshot_id, progress=j.progress,
                  total_items=j.total_items, done_items=j.done_items,
                  failed_items=j.failed_items, summary=j.summary, versions=j.versions,
                  params=j.params, error=j.error, started_at=j.started_at,
                  finished_at=j.finished_at, status_url=f"/api/v1/analysis/jobs/{j.id}",
                  results_url=f"/api/v1/analysis/jobs/{j.id}/results")


def _record(r: DecisionRecord, full: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "instrument_id": r.instrument_id, "symbol": r.symbol, "exchange": r.exchange,
        "name": r.name, "industry": r.industry, "route": f"/stocks/{r.exchange}/{r.symbol}",
        "rank": r.rank, "state": r.state, "holding_state": r.holding_state,
        "coverage": r.coverage, "opportunity": r.opportunity, "uncertainty": r.uncertainty,
        "pillars": r.pillars, "supporting": r.supporting, "counter": r.counter,
        "overrides": r.overrides, "explanation": r.explanation, "as_of": r.as_of.isoformat(),
        "horizon": r.horizon, "review_by": r.review_by.isoformat(),
        "data_as_of": r.data_as_of}
    if full:
        out |= {"features": r.features, "missing": r.missing, "versions": r.versions,
                "disclaimer": "Research classification from a versioned rule set, not "
                              "investment advice or a guaranteed outcome."}
    return out


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
def create_scan(body: ScanIn, request: Request, p: WriterDep, session: SessionDep,
                lake: LakeDep,
                idempotency_key: Annotated[str | None, Header(max_length=80)] = None
                ) -> JSONResponse:
    """Queue a batch scan. Returns 202 with the job id; poll status_url for progress."""
    if body.universe_id is not None:
        snap = usvc.resolve(session, p, lake, body.universe_id, usvc.SnapshotIn(),
                            request_id(request))
    else:
        assert body.universe_snapshot_id is not None
        snap = usvc.get_snapshot(session, p, body.universe_snapshot_id)
    job, created = queue_scan(session, p, lake, snap, body.as_of,
                              body.policy.model_dump(exclude_none=True), idempotency_key,
                              request_id(request))
    out = _job(job).model_dump(mode="json")
    return JSONResponse(out, status_code=202 if created else 200,
                        headers={"Location": out["status_url"]})


def queue_scan(session: Session, p: Principal, lake: LakeClient, snap: UniverseSnapshot,
               as_of_in: date | None,
               policy: dict[str, Any], idempotency_key: str | None, rid: str | None,
               extra: dict[str, Any] | None = None) -> tuple[AnalysisJob, bool]:
    """Point-in-time checks, then enqueue a SCAN of ``snap`` (shared by portfolio reviews)."""
    if snap.member_count == 0:
        raise DomainError("The universe snapshot has no resolved members")
    as_of = as_of_in or latest_market_date(lake, date.today())
    kind = str(snap.definition.get("kind") or "")
    notes: list[str] = []
    lag = ((snap.source_snapshot_date - as_of).days
           if kind in ("INDEX", "SECTOR", "HOLDINGS") and snap.source_snapshot_date else 0)
    if 0 < lag <= MEMBERSHIP_TOLERANCE_DAYS:
        notes.append(f"{kind.lower()} membership list is dated {snap.source_snapshot_date}, "
                     f"{lag} days after as_of; NSE announces changes weeks ahead, so this is "
                     "treated as known at as_of")
    elif lag > MEMBERSHIP_TOLERANCE_DAYS:
        raise DomainError(
            f"The {kind.lower()} membership is dated {snap.source_snapshot_date}, after "
            f"as_of {as_of}: using it would leak future membership into the past "
            "(survivorship bias). Use a manual list or a later as_of.")
    params = {"as_of": as_of.isoformat(), "universe_snapshot_id": str(snap.id),
              "policy": policy, "notes": notes} | (extra or {})
    job, created = jobs.enqueue(session, p, "SCAN", params, idempotency_key=idempotency_key,
                                request_id=rid, universe_snapshot_id=snap.id,
                                as_of=as_of, total_items=snap.member_count)
    return job, created


@router.get("/jobs")
def list_jobs(p: PrincipalDep, session: SessionDep,
              limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[JobOut]:
    rows = session.scalars(select(AnalysisJob).where(AnalysisJob.workspace_id == p.workspace_id)
                           .order_by(AnalysisJob.created_at.desc()).limit(limit))
    return [_job(j) for j in rows]


@router.get("/jobs/{job_id}")
def get_job(job_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> JobOut:
    return _job(jobs.get(session, p, job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: uuid.UUID, p: WriterDep, session: SessionDep) -> JobOut:
    return _job(jobs.cancel(session, p, job_id))


@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_job(job_id: uuid.UUID, p: WriterDep, session: SessionDep) -> JobOut:
    return _job(jobs.retry(session, p, job_id))


def _cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode()


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return int(json.loads(base64.urlsafe_b64decode(cursor.encode()))["o"])
    except Exception as exc:
        raise DomainError("Malformed cursor") from exc


SORTS = {"rank": DecisionRecord.rank, "opportunity": DecisionRecord.opportunity,
         "uncertainty": DecisionRecord.uncertainty, "symbol": DecisionRecord.symbol}


def _results_query(job_id: uuid.UUID, state: list[str] | None, coverage: str | None,
                   industry: str | None, min_opportunity: float | None, q: str | None) -> Any:
    sel = select(DecisionRecord).where(DecisionRecord.job_id == job_id)
    if state:
        sel = sel.where(DecisionRecord.state.in_(state))
    if coverage:
        sel = sel.where(DecisionRecord.coverage == coverage.upper())
    if industry:
        sel = sel.where(func.lower(DecisionRecord.industry) == industry.lower())
    if min_opportunity is not None:
        sel = sel.where(DecisionRecord.opportunity >= min_opportunity)
    if q:
        sel = sel.where(DecisionRecord.symbol.ilike(f"%{q.upper()}%"))
    return sel


@router.get("/jobs/{job_id}/results")
def results(job_id: uuid.UUID, p: PrincipalDep, session: SessionDep,
            state: Annotated[list[str] | None, Query()] = None,
            coverage: str | None = None, industry: str | None = None,
            min_opportunity: Annotated[float | None, Query(ge=0, le=100)] = None,
            q: Annotated[str | None, Query(max_length=40)] = None,
            sort: Literal["rank", "opportunity", "uncertainty", "symbol"] = "rank",
            order: Literal["asc", "desc"] = "asc",
            limit: Annotated[int, Query(ge=1, le=200)] = 50,
            cursor: str | None = None) -> dict[str, Any]:
    job = jobs.get(session, p, job_id)
    for st in state or []:
        if st not in STATES:
            raise DomainError(f"Unknown state {st!r}")
    sel = _results_query(job_id, state, coverage, industry, min_opportunity, q)
    total = session.scalar(select(func.count()).select_from(sel.subquery())) or 0
    col = SORTS[sort]
    off = _offset(cursor)
    rows = list(session.scalars(sel.order_by(
        col.desc().nulls_last() if order == "desc" else col.asc().nulls_last(),
        DecisionRecord.rank).offset(off).limit(limit + 1)))
    more = len(rows) > limit
    return {"job": {"id": str(job.id), "status": job.status, "as_of": job.as_of,
                    "versions": job.versions, "summary": job.summary},
            "total": total, "next_cursor": _cursor(off + limit) if more else None,
            "data": [_record(r) for r in rows[:limit]],
            "notes": ["States are research classifications, not advice. Rows marked STALE or "
                      "INSUFFICIENT are not ranked against complete rows."]}


@router.get("/jobs/{job_id}/results/{symbol}")
def result_detail(job_id: uuid.UUID, symbol: str, p: PrincipalDep,
                  session: SessionDep) -> dict[str, Any]:
    jobs.get(session, p, job_id)
    r = session.scalar(select(DecisionRecord).where(DecisionRecord.job_id == job_id,
                                                    DecisionRecord.symbol == symbol.upper()))
    if r is None:
        raise NotFound(f"{symbol} is not in this job")
    return _record(r, full=True)


def _safe(v: Any) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s  # formula injection


@router.get("/jobs/{job_id}/export.csv")
def export_csv(job_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> Response:
    job = jobs.get(session, p, job_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    pillars = ["MOMENTUM", "QUALITY", "GROWTH", "VALUATION", "RISK"]
    w.writerow(["rank", "symbol", "name", "industry", "state", "coverage", "opportunity",
                "uncertainty", *pillars, "explanation", "as_of", "policy"])
    for r in session.scalars(select(DecisionRecord).where(DecisionRecord.job_id == job_id)
                             .order_by(DecisionRecord.rank)):
        w.writerow([_safe(x) for x in (r.rank, r.symbol, r.name, r.industry, r.state,
                                       r.coverage, r.opportunity, r.uncertainty,
                                       *(r.pillars.get(k) for k in pillars), r.explanation,
                                       r.as_of, r.versions.get("policy"))])
    return Response(buf.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="scan_{job.id}.csv"'})


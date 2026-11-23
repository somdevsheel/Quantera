"""Durable job queue on PostgreSQL.

* enqueue: idempotent per (workspace, Idempotency-Key); per-workspace quota of open jobs.
* claim:   SELECT ... FOR UPDATE SKIP LOCKED, so several workers never take the same job.
* stalled: a RUNNING job without a heartbeat for STALL_SECONDS is re-queued, up to
           MAX_ATTEMPTS, then FAILED. Nothing is lost when a worker dies.
* cancel:  sets a flag; the runner checks it between items and stops with CANCELLED.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from quantera.analysis.models import AnalysisJob
from quantera.core import audit
from quantera.core.errors import Conflict, NotFound, RateLimited
from quantera.identity.service import Principal

MAX_OPEN_JOBS = 3
MAX_ATTEMPTS = 3
STALL_SECONDS = 600
OPEN = ("QUEUED", "RUNNING")
FINAL = ("SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED")


def now() -> datetime:
    return datetime.now(UTC)


def enqueue(session: Session, p: Principal, kind: str, params: dict[str, Any], *,
            idempotency_key: str | None, request_id: str | None = None,
            **fields: Any) -> tuple[AnalysisJob, bool]:
    """(job, created). The same key with the same params returns the existing job."""
    canonical = json.loads(json.dumps(params, sort_keys=True, default=str))
    if idempotency_key:
        old = session.scalar(select(AnalysisJob).where(
            AnalysisJob.workspace_id == p.workspace_id,
            AnalysisJob.idempotency_key == idempotency_key))
        if old is not None:
            if old.kind != kind or old.params != canonical:
                raise Conflict("This Idempotency-Key was used for a different request")
            return old, False
    open_jobs = session.scalar(select(func.count()).select_from(AnalysisJob).where(
        AnalysisJob.workspace_id == p.workspace_id, AnalysisJob.status.in_(OPEN))) or 0
    if open_jobs >= MAX_OPEN_JOBS:
        raise RateLimited(f"{open_jobs} jobs are already queued or running; the limit is "
                          f"{MAX_OPEN_JOBS} per workspace")
    job = AnalysisJob(workspace_id=p.workspace_id, created_by=p.user_id, kind=kind,
                      status="QUEUED", params=canonical, idempotency_key=idempotency_key,
                      **fields)
    session.add(job)
    session.flush()
    audit.record(session, f"job.{kind.lower()}.create", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="analysis_job", resource_id=job.id, request_id=request_id)
    return job, True


def get(session: Session, p: Principal, job_id: uuid.UUID) -> AnalysisJob:
    job = session.get(AnalysisJob, job_id)
    if job is None or job.workspace_id != p.workspace_id:
        raise NotFound("Job not found")
    return job


def cancel(session: Session, p: Principal, job_id: uuid.UUID) -> AnalysisJob:
    job = get(session, p, job_id)
    if job.status in FINAL:
        raise Conflict(f"Job already {job.status}")
    if job.status == "QUEUED":
        job.status, job.finished_at = "CANCELLED", now()
    job.cancel_requested = True
    return job


def retry(session: Session, p: Principal, job_id: uuid.UUID) -> AnalysisJob:
    job = get(session, p, job_id)
    if job.status not in ("FAILED", "CANCELLED", "PARTIAL"):
        raise Conflict(f"Only FAILED, CANCELLED or PARTIAL jobs can be retried ({job.status})")
    new, _ = enqueue(session, p, job.kind, job.params, idempotency_key=None,
                     universe_snapshot_id=job.universe_snapshot_id, as_of=job.as_of)
    new.summary = {"retry_of": str(job.id)}
    return new


def requeue_stalled(session: Session) -> int:
    limit = now() - timedelta(seconds=STALL_SECONDS)
    stalled = list(session.scalars(select(AnalysisJob).where(
        AnalysisJob.status == "RUNNING", AnalysisJob.heartbeat_at < limit).with_for_update(
        skip_locked=True)))
    for j in stalled:
        if j.attempts >= MAX_ATTEMPTS:
            j.status, j.finished_at = "FAILED", now()
            j.error = f"worker stopped responding {j.attempts} times"
        else:
            j.status = "QUEUED"
    return len(stalled)


def claim(session: Session, kinds: tuple[str, ...] | None = None) -> AnalysisJob | None:
    q = select(AnalysisJob).where(AnalysisJob.status == "QUEUED")
    if kinds:
        q = q.where(AnalysisJob.kind.in_(kinds))
    job = session.scalar(q.order_by(AnalysisJob.created_at).limit(1)
                         .with_for_update(skip_locked=True))
    if job is None:
        return None
    job.status, job.attempts = "RUNNING", job.attempts + 1
    job.started_at = job.started_at or now()
    job.heartbeat_at = now()
    session.commit()
    return job


class Progress:
    """Passed to runners: records progress and answers whether the job was cancelled.
    Uses its own short transactions so progress is visible while the job runs."""

    def __init__(self, sessions: Callable[[], Session], job_id: uuid.UUID) -> None:
        self.sessions, self.job_id = sessions, job_id

    def update(self, done: int, total: int, failed: int = 0) -> bool:
        """Record progress; returns False when cancellation was requested."""
        with self.sessions() as s:
            s.execute(update(AnalysisJob).where(AnalysisJob.id == self.job_id).values(
                done_items=done, total_items=total, failed_items=failed,
                progress=round(done / total, 4) if total else 0.0, heartbeat_at=now()))
            cancelled = bool(s.scalar(select(AnalysisJob.cancel_requested).where(
                AnalysisJob.id == self.job_id)))
            s.commit()
        return not cancelled


class Cancelled(Exception):
    pass

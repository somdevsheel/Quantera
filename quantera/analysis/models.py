"""Background analysis jobs (durable queue in PostgreSQL) and immutable decision records."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from quantera.core.db import Base, CreatedAt, UUIDPk

JOB_STATES = ("QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED")


class AnalysisJob(UUIDPk, CreatedAt, Base):
    """A long-running request. The API returns 202 with its id; a worker claims it with
    SELECT ... FOR UPDATE SKIP LOCKED, heartbeats while running and records progress.
    A job whose heartbeat stops is re-queued (bounded attempts)."""

    __tablename__ = "analysis_jobs"
    __table_args__ = (
        Index("ix_analysis_jobs_queue", "status", "created_at"),
        Index("ix_analysis_jobs_workspace", "workspace_id", "created_at"),
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_analysis_jobs_idem"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(20))  # SCAN | BACKTEST | TRAIN | OPTIMIZE ...
    status: Mapped[str] = mapped_column(String(12), default="QUEUED")
    params: Mapped[dict[str, Any]] = mapped_column(JSON)
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    universe_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("universe_snapshots.id", ondelete="SET NULL"))
    as_of: Mapped[date | None] = mapped_column(Date)
    versions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    done_items: Mapped[int] = mapped_column(Integer, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(1000))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DecisionRecord(UUIDPk, CreatedAt, Base):
    """One instrument's research classification from one job. Immutable: it carries every
    version needed to reproduce it (PRD Appendix A)."""

    __tablename__ = "decision_records"
    __table_args__ = (
        Index("ix_decision_records_job_state", "job_id", "state"),
        Index("ix_decision_records_instrument", "workspace_id", "instrument_id", "as_of"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"))
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("analysis_jobs.id", ondelete="CASCADE"))
    instrument_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(40))
    exchange: Mapped[str] = mapped_column(String(8))
    name: Mapped[str | None] = mapped_column(String(200))
    industry: Mapped[str | None] = mapped_column(String(120))
    as_of: Mapped[date] = mapped_column(Date)
    horizon: Mapped[str] = mapped_column(String(10))
    review_by: Mapped[date] = mapped_column(Date)
    state: Mapped[str] = mapped_column(String(20))
    holding_state: Mapped[str | None] = mapped_column(String(20))
    coverage: Mapped[str] = mapped_column(String(12))
    rank: Mapped[int] = mapped_column(Integer)
    opportunity: Mapped[float | None] = mapped_column(Float)
    uncertainty: Mapped[float] = mapped_column(Float)
    pillars: Mapped[dict[str, Any]] = mapped_column(JSON)
    features: Mapped[dict[str, Any]] = mapped_column(JSON)
    supporting: Mapped[list[str]] = mapped_column(JSON)
    counter: Mapped[list[str]] = mapped_column(JSON)
    missing: Mapped[list[str]] = mapped_column(JSON)
    overrides: Mapped[list[str]] = mapped_column(JSON)
    explanation: Mapped[str] = mapped_column(String(1000))
    data_as_of: Mapped[dict[str, Any]] = mapped_column(JSON)
    versions: Mapped[dict[str, Any]] = mapped_column(JSON)

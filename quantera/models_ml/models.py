"""Model registry and stored predictions (for monitoring against realised outcomes)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
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


class MlModel(UUIDPk, CreatedAt, Base):
    """Immutable once trained: only the stage, approval fields and history change.
    Stages: CANDIDATE (all gates passed, awaiting approval) | REJECTED | APPROVED (the champion
    of its name) | RETIRED."""

    __tablename__ = "ml_models"
    __table_args__ = (UniqueConstraint("workspace_id", "name", "version",
                                       name="uq_ml_models_name_version"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("analysis_jobs.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(12))
    model_type: Mapped[str] = mapped_column(String(12))
    target: Mapped[str] = mapped_column(String(120))
    horizon: Mapped[int] = mapped_column(Integer)
    features: Mapped[list[str]] = mapped_column(JSON)
    params: Mapped[dict[str, Any]] = mapped_column(JSON)
    dataset: Mapped[dict[str, Any]] = mapped_column(JSON)
    validation: Mapped[dict[str, Any]] = mapped_column(JSON)
    gates: Mapped[dict[str, Any]] = mapped_column(JSON)
    reference: Mapped[dict[str, Any]] = mapped_column(JSON)
    code_version: Mapped[str] = mapped_column(String(40))
    artifact_path: Mapped[str] = mapped_column(String(300))
    artifact_sha256: Mapped[str] = mapped_column(String(64))
    label_cutoff: Mapped[date] = mapped_column(Date)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSON)


class MlPrediction(UUIDPk, CreatedAt, Base):
    __tablename__ = "ml_predictions"
    __table_args__ = (
        Index("ix_ml_predictions_model_asof", "model_id", "as_of"),
        Index("ix_ml_predictions_instrument", "workspace_id", "instrument_id", "as_of"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"))
    model_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ml_models.id", ondelete="CASCADE"))
    as_of: Mapped[date] = mapped_column(Date)
    instrument_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(40))
    score: Mapped[float] = mapped_column(Float)
    percentile: Mapped[float] = mapped_column(Float)
    interval_low: Mapped[float | None] = mapped_column(Float)
    interval_high: Mapped[float | None] = mapped_column(Float)
    matures_on: Mapped[date] = mapped_column(Date)
    realised: Mapped[float | None] = mapped_column(Float)

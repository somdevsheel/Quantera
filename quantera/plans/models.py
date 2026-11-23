"""Saved trade plans (protective rules on a position) and their append-only event history."""

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


class TradePlan(UUIDPk, CreatedAt, Base):
    __tablename__ = "trade_plans"
    __table_args__ = (
        Index("ix_trade_plans_ws_status", "workspace_id", "status"),
        UniqueConstraint("workspace_id", "client_ref", name="uq_trade_plans_client_ref"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    client_ref: Mapped[str] = mapped_column(String(64))
    portfolio_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("portfolios.id", ondelete="SET NULL"))
    instrument_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(40))
    exchange: Mapped[str] = mapped_column(String(8))
    side: Mapped[str] = mapped_column(String(5))
    status: Mapped[str] = mapped_column(String(12))  # DRAFT|ACTIVE|PAUSED|CLOSED|CANCELLED
    entry_price: Mapped[float] = mapped_column(Float)
    entry_date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[float] = mapped_column(Float)
    books_paper: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    last_price: Mapped[float | None] = mapped_column(Float)
    evaluated_through: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, default=1)
    policy_version: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TradePlanEvent(UUIDPk, CreatedAt, Base):
    __tablename__ = "trade_plan_events"
    __table_args__ = (
        UniqueConstraint("plan_id", "idempotency_key", name="uq_trade_plan_events_key"),
        Index("ix_trade_plan_events_plan", "plan_id", "seq"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"))
    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("trade_plans.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    event: Mapped[str] = mapped_column(String(30))
    rule: Mapped[str | None] = mapped_column(String(30))
    bar_date: Mapped[date | None] = mapped_column(Date)
    price: Mapped[float | None] = mapped_column(Float)
    quantity: Mapped[float | None] = mapped_column(Float)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    idempotency_key: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict[str, Any]] = mapped_column(JSON)

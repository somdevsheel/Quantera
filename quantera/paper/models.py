"""Paper orders, their state history, and portfolio risk policies with limit breaches."""

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


class PaperOrder(UUIDPk, CreatedAt, Base):
    __tablename__ = "paper_orders"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "client_order_id", name="uq_paper_orders_client_id"),
        Index("ix_paper_orders_pf_status", "portfolio_id", "status"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"))
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("portfolios.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    client_order_id: Mapped[str] = mapped_column(String(64))
    instrument_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(40))
    exchange: Mapped[str] = mapped_column(String(8))
    side: Mapped[str] = mapped_column(String(4))
    order_type: Mapped[str] = mapped_column(String(12))
    quantity: Mapped[float] = mapped_column(Float)
    limit_price: Mapped[float | None] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float)
    tif: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(18))
    filled_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    average_price: Mapped[float | None] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    triggered: Mapped[bool] = mapped_column(default=False)
    sessions: Mapped[int] = mapped_column(Integer, default=0)
    placed_on: Mapped[date] = mapped_column(Date)
    simulated_through: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(String(300))
    version: Mapped[int] = mapped_column(Integer, default=1)


class PaperOrderEvent(UUIDPk, CreatedAt, Base):
    __tablename__ = "paper_order_events"
    __table_args__ = (Index("ix_paper_order_events_order", "order_id", "seq"),)

    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("paper_orders.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    event: Mapped[str] = mapped_column(String(30))
    from_status: Mapped[str | None] = mapped_column(String(18))
    to_status: Mapped[str | None] = mapped_column(String(18))
    bar_date: Mapped[date | None] = mapped_column(Date)
    details: Mapped[dict[str, Any]] = mapped_column(JSON)


class RiskPolicy(UUIDPk, CreatedAt, Base):
    __tablename__ = "risk_policies"
    __table_args__ = (UniqueConstraint("portfolio_id", name="uq_risk_policies_portfolio"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"))
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("portfolios.id", ondelete="CASCADE"))
    daily_loss_limit: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    max_position_weight: Mapped[float | None] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String(10), default="NORMAL")  # NORMAL | RISK_OFF
    state_reason: Mapped[str | None] = mapped_column(String(300))
    nav_peak: Mapped[float | None] = mapped_column(Float)
    last_nav: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RiskBreach(UUIDPk, CreatedAt, Base):
    __tablename__ = "risk_breaches"
    __table_args__ = (UniqueConstraint("policy_id", "kind", "as_of",
                                       name="uq_risk_breaches_day"),)

    policy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("risk_policies.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    as_of: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Float)
    limit: Mapped[float] = mapped_column(Float)
    action: Mapped[str] = mapped_column(String(12))

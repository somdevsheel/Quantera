"""Portfolios and their append-only transaction ledger (private to a workspace)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
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

TX_TYPES = ("BUY", "SELL", "DEPOSIT", "WITHDRAWAL", "DIVIDEND", "FEE")


class Portfolio(UUIDPk, CreatedAt, Base):
    __tablename__ = "portfolios"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_portfolios_ws_name"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(120))
    base_currency: Mapped[str] = mapped_column(String(3), default="INR")
    is_paper: Mapped[bool] = mapped_column(Boolean, default=False)
    max_position_weight: Mapped[float] = mapped_column(Float, default=0.10)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class PortfolioTransaction(UUIDPk, CreatedAt, Base):
    """Never edited or deleted: a mistake is corrected by voiding (kept, flagged) and
    re-entering. ``external_ref`` makes imports and paper fills idempotent."""

    __tablename__ = "portfolio_transactions"
    __table_args__ = (
        Index("ix_portfolio_transactions_pf", "portfolio_id", "trade_date", "seq"),
        UniqueConstraint("portfolio_id", "external_ref", name="uq_portfolio_tx_ref"),
    )

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("portfolios.id", ondelete="CASCADE"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    trade_date: Mapped[date] = mapped_column(Date)
    type: Mapped[str] = mapped_column(String(12))
    instrument_id: Mapped[str | None] = mapped_column(String(64))
    symbol: Mapped[str | None] = mapped_column(String(40))
    exchange: Mapped[str | None] = mapped_column(String(8))
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str | None] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(20), default="manual")
    external_ref: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    void_reason: Mapped[str | None] = mapped_column(String(300))

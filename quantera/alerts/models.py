"""Alert rules and the events they raise (deduplicated per rule and bar)."""

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


class AlertRule(UUIDPk, CreatedAt, Base):
    __tablename__ = "alert_rules"
    __table_args__ = (Index("ix_alert_rules_ws", "workspace_id", "enabled"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(24))
    instrument_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(40))
    exchange: Mapped[str] = mapped_column(String(8))
    threshold: Mapped[float] = mapped_column(Float)
    cooldown_days: Mapped[int] = mapped_column(Integer, default=1)
    channels: Mapped[list[str]] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_triggered_on: Mapped[date | None] = mapped_column(Date)


class AlertEvent(UUIDPk, CreatedAt, Base):
    __tablename__ = "alert_events"
    __table_args__ = (
        UniqueConstraint("rule_id", "bar_date", name="uq_alert_events_rule_bar"),
        Index("ix_alert_events_ws", "workspace_id", "created_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"))
    rule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("alert_rules.id", ondelete="CASCADE"))
    bar_date: Mapped[date] = mapped_column(Date)
    value: Mapped[float | None] = mapped_column(Float)
    message: Mapped[str] = mapped_column(String(300))
    details: Mapped[dict[str, Any]] = mapped_column(JSON)
    delivery: Mapped[dict[str, Any]] = mapped_column(JSON)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

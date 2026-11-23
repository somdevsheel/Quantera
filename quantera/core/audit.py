"""Append-only audit events for sensitive actions (auth, permissions, sharing, promotions)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantera.core.db import Base, CreatedAt, UUIDPk


class AuditEvent(UUIDPk, CreatedAt, Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_workspace_created", "workspace_id", "created_at"),)

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(80))
    resource_type: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(20), default="success")
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


def record(session: Session, action: str, *, actor: uuid.UUID | None = None,
           workspace: uuid.UUID | None = None, resource_type: str | None = None,
           resource_id: str | uuid.UUID | None = None, result: str = "success",
           request_id: str | None = None, details: dict[str, Any] | None = None) -> None:
    """``details`` must not contain secrets or unnecessary personal data."""
    session.add(AuditEvent(actor_user_id=actor, workspace_id=workspace, action=action,
                           resource_type=resource_type,
                           resource_id=str(resource_id) if resource_id else None,
                           result=result, request_id=request_id, details=details or {}))

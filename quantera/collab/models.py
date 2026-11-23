"""Invitations to group workspaces and explicit, scoped, revocable share grants."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from quantera.core.db import Base, CreatedAt, UUIDPk


class Invitation(UUIDPk, CreatedAt, Base):
    __tablename__ = "invitations"
    __table_args__ = (Index("ix_invitations_ws", "workspace_id"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(10))  # MEMBER | GUEST
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ShareGrant(UUIDPk, CreatedAt, Base):
    """Read-only access to ONE resource, for one user or one group workspace, until it expires
    or is revoked. Portfolios are never shareable."""

    __tablename__ = "share_grants"
    __table_args__ = (Index("ix_share_grants_grantee", "grantee_user_id", "grantee_workspace_id"),)

    owner_workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    resource_type: Mapped[str] = mapped_column(String(20))  # WATCHLIST | SCAN | BACKTEST
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    grantee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    grantee_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"))
    permission: Mapped[str] = mapped_column(String(10), default="READ")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

"""Approval records for portfolio-construction proposals (PRD 13: explicit approval, paper only)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from quantera.core.db import Base, CreatedAt, UUIDPk


class ProposalApproval(UUIDPk, CreatedAt, Base):
    __tablename__ = "proposal_approvals"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("analysis_jobs.id", ondelete="CASCADE"), unique=True)
    status: Mapped[str] = mapped_column(String(20))  # APPROVED_PAPER | REJECTED
    decided_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str] = mapped_column(String(500))
    paper_portfolio_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("portfolios.id", ondelete="SET NULL"))

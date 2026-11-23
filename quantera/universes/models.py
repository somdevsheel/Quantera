"""Universes (index, sector, manual/CSV, watchlist) and their immutable resolved snapshots.

A universe is a definition. Resolving it against the lake produces a UniverseSnapshot: the exact
members, the lake release and snapshot date used, unresolved inputs and a content hash. Scans and
backtests reference a snapshot, never a live definition, so results are reproducible.
"""

from __future__ import annotations

import uuid
from datetime import date
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Date, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from quantera.core.db import Base, CreatedAt, UUIDPk


class UniverseKind(StrEnum):
    INDEX = "INDEX"  # NSE index constituents (lake snapshots)
    SECTOR = "SECTOR"  # NSE industry classification
    MANUAL = "MANUAL"  # symbols typed in or imported from CSV
    WATCHLIST = "WATCHLIST"  # the current items of a watchlist
    HOLDINGS = "HOLDINGS"  # a portfolio's holdings (stage 5)


class Watchlist(UUIDPk, CreatedAt, Base):
    __tablename__ = "watchlists"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_watchlists_workspace_name"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(120))


class WatchlistItem(UUIDPk, CreatedAt, Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("watchlist_id", "instrument_id",
                                       name="uq_watchlist_items_instrument"),)

    watchlist_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("watchlists.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[str] = mapped_column(String(64))  # lake canonical id, e.g. IN:INE...
    symbol: Mapped[str] = mapped_column(String(40))
    exchange: Mapped[str] = mapped_column(String(8))  # MIC
    name: Mapped[str | None] = mapped_column(String(200))


class Universe(UUIDPk, CreatedAt, Base):
    __tablename__ = "universes"
    __table_args__ = (Index("ix_universes_workspace", "workspace_id"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(12))
    # INDEX: {"index"}; SECTOR: {"industry"}; MANUAL: {"symbols", "exchange", "source"};
    # WATCHLIST: {"watchlist_id"}
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class UniverseSnapshot(UUIDPk, CreatedAt, Base):
    """Immutable: never updated after creation."""

    __tablename__ = "universe_snapshots"
    __table_args__ = (Index("ix_universe_snapshots_universe", "universe_id", "created_at"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"))
    universe_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("universes.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    requested_as_of: Mapped[date | None] = mapped_column(Date)
    source_snapshot_date: Mapped[date | None] = mapped_column(Date)  # lake list date used
    lake_release: Mapped[str] = mapped_column(String(40))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)  # kind + params at resolution time
    members: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    unresolved: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    member_count: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    notes: Mapped[list[str]] = mapped_column(JSON, default=list)

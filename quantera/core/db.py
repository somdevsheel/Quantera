"""SQLAlchemy base, engine and sessions for the operational PostgreSQL database."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, Uuid, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class UUIDPk:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 nullable=False)


class Database:
    def __init__(self, url: str, schema: str | None = None) -> None:
        self.engine: Engine = create_engine(url, pool_pre_ping=True, future=True)
        if schema:
            @event.listens_for(self.engine, "connect")
            def _search_path(dbapi_conn, _record):  # type: ignore[no-untyped-def]
                with dbapi_conn.cursor() as cur:
                    cur.execute(f'SET search_path TO "{schema}"')
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> Iterator[Session]:
        with self.sessions() as s:
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise

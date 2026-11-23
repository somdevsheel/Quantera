"""/api/v1/universes and /api/v1/watchlists."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from quantera.api.deps import PrincipalDep, SessionDep, WriterDep, request_id
from quantera.lake.client import LakeClient
from quantera.universes import service as svc
from quantera.universes.models import Universe, UniverseSnapshot, Watchlist, WatchlistItem

router = APIRouter(tags=["universes"])


def get_lake(request: Request) -> LakeClient:
    lake: LakeClient = request.app.state.lake
    return lake


LakeDep = Annotated[LakeClient, Depends(get_lake)]


class UniverseOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    params: dict[str, Any]
    created_at: datetime


class SnapshotOut(BaseModel):
    id: uuid.UUID
    universe_id: uuid.UUID
    created_at: datetime
    requested_as_of: date | None
    source_snapshot_date: date | None
    lake_release: str
    member_count: int
    content_hash: str
    notes: list[str]
    unresolved: list[dict[str, Any]]
    members: list[dict[str, Any]] | None = None


class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ItemIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    exchange: str = "NSE"


class ItemOut(BaseModel):
    id: uuid.UUID
    instrument_id: str
    symbol: str
    exchange: str
    name: str | None


class WatchlistOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    items: list[ItemOut] | None = None


def _u(u: Universe) -> UniverseOut:
    return UniverseOut(id=u.id, name=u.name, kind=u.kind, params=u.params, created_at=u.created_at)


def _s(s: UniverseSnapshot, members: bool = False) -> SnapshotOut:
    return SnapshotOut(id=s.id, universe_id=s.universe_id, created_at=s.created_at,
                       requested_as_of=s.requested_as_of,
                       source_snapshot_date=s.source_snapshot_date, lake_release=s.lake_release,
                       member_count=s.member_count, content_hash=s.content_hash, notes=s.notes,
                       unresolved=s.unresolved, members=s.members if members else None)


@router.get("/universes/catalog")
def catalog(p: PrincipalDep, lake: LakeDep) -> dict[str, Any]:
    """Index and industry lists available in the lake, with their snapshot dates."""
    return {"indices": lake.indices(), "industries": lake.industries(),
            "notes": ["Index and industry lists are point-in-time snapshots; a date before the "
                      "first snapshot is refused rather than back-filled (survivorship bias)."]}


@router.post("/universes", status_code=status.HTTP_201_CREATED)
def create_universe(body: svc.UniverseIn, request: Request, p: WriterDep,
                    session: SessionDep) -> UniverseOut:
    return _u(svc.create(session, p, body, request_id(request)))


@router.post("/universes/import-csv", status_code=status.HTTP_201_CREATED)
async def import_csv(request: Request, p: WriterDep, session: SessionDep,
                     name: Annotated[str, Form(min_length=1, max_length=120)],
                     file: Annotated[UploadFile, File()],
                     exchange: Annotated[str, Form()] = "NSE") -> UniverseOut:
    """CSV with a `symbol` column (or symbols in the first column). Cells that look like
    spreadsheet formulas are rejected, not imported."""
    data = await file.read(svc.MAX_CSV_BYTES + 1)
    return _u(svc.import_csv(session, p, name, data, exchange, request_id(request)))


@router.get("/universes")
def list_universes(p: PrincipalDep, session: SessionDep) -> list[UniverseOut]:
    rows = session.scalars(select(Universe).where(Universe.workspace_id == p.workspace_id)
                           .order_by(Universe.created_at.desc()))
    return [_u(u) for u in rows]


@router.get("/universes/{universe_id}")
def get_universe(universe_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> UniverseOut:
    return _u(svc._owned(session, p, universe_id))


@router.delete("/universes/{universe_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_universe(universe_id: uuid.UUID, request: Request, p: WriterDep,
                    session: SessionDep) -> None:
    svc.delete(session, p, universe_id, request_id(request))


@router.post("/universes/{universe_id}/snapshots", status_code=status.HTTP_201_CREATED)
def snapshot(universe_id: uuid.UUID, body: svc.SnapshotIn, request: Request, p: WriterDep,
             session: SessionDep, lake: LakeDep) -> SnapshotOut:
    """Resolve the universe against the lake into an immutable, hashed snapshot."""
    return _s(svc.resolve(session, p, lake, universe_id, body, request_id(request)), True)


@router.get("/universes/{universe_id}/snapshots")
def list_snapshots(universe_id: uuid.UUID, p: PrincipalDep, session: SessionDep
                   ) -> list[SnapshotOut]:
    svc._owned(session, p, universe_id)
    rows = session.scalars(select(UniverseSnapshot).where(
        UniverseSnapshot.universe_id == universe_id).order_by(UniverseSnapshot.created_at.desc()))
    return [_s(s) for s in rows]


@router.get("/universe-snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> SnapshotOut:
    return _s(svc.get_snapshot(session, p, snapshot_id), True)


@router.post("/watchlists", status_code=status.HTTP_201_CREATED)
def create_watchlist(body: WatchlistIn, p: WriterDep, session: SessionDep) -> WatchlistOut:
    w = svc.create_watchlist(session, p, body.name)
    return WatchlistOut(id=w.id, name=w.name, created_at=w.created_at, items=[])


@router.get("/watchlists")
def list_watchlists(p: PrincipalDep, session: SessionDep) -> list[WatchlistOut]:
    rows = session.scalars(select(Watchlist).where(Watchlist.workspace_id == p.workspace_id)
                           .order_by(Watchlist.name))
    return [WatchlistOut(id=w.id, name=w.name, created_at=w.created_at) for w in rows]


@router.get("/watchlists/{watchlist_id}")
def get_watchlist(watchlist_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> WatchlistOut:
    w = svc._watchlist(session, p, watchlist_id)
    items = session.scalars(select(WatchlistItem).where(WatchlistItem.watchlist_id == w.id)
                            .order_by(WatchlistItem.symbol))
    return WatchlistOut(id=w.id, name=w.name, created_at=w.created_at,
                        items=[ItemOut(id=i.id, instrument_id=i.instrument_id, symbol=i.symbol,
                                       exchange=i.exchange, name=i.name) for i in items])


@router.delete("/watchlists/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watchlist(watchlist_id: uuid.UUID, p: WriterDep, session: SessionDep) -> None:
    svc.delete_watchlist(session, p, watchlist_id)


@router.post("/watchlists/{watchlist_id}/items", status_code=status.HTTP_201_CREATED)
def add_item(watchlist_id: uuid.UUID, body: ItemIn, p: WriterDep, session: SessionDep,
             lake: LakeDep) -> ItemOut:
    i = svc.add_item(session, p, lake, watchlist_id, body.symbol, body.exchange)
    return ItemOut(id=i.id, instrument_id=i.instrument_id, symbol=i.symbol, exchange=i.exchange,
                   name=i.name)


@router.delete("/watchlists/{watchlist_id}/items/{item_id}",
               status_code=status.HTTP_204_NO_CONTENT)
def remove_item(watchlist_id: uuid.UUID, item_id: uuid.UUID, p: WriterDep,
                session: SessionDep) -> None:
    svc.remove_item(session, p, watchlist_id, item_id)

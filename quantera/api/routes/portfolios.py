"""/api/v1/portfolios: ledger, holdings import, valuation, risk, holding review."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Header, Request, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from quantera.api.deps import PrincipalDep, SessionDep, WriterDep, request_id
from quantera.api.routes.analysis import _job, queue_scan
from quantera.api.routes.universes import LakeDep
from quantera.portfolios import service as svc
from quantera.portfolios.models import Portfolio, PortfolioTransaction
from quantera.universes import service as usvc
from quantera.universes.models import Universe, UniverseKind

router = APIRouter(prefix="/portfolios", tags=["portfolios"])


class PortfolioOut(BaseModel):
    id: uuid.UUID
    name: str
    base_currency: str
    is_paper: bool
    max_position_weight: float
    created_at: datetime


class TxOut(BaseModel):
    id: uuid.UUID
    seq: int
    trade_date: date
    type: str
    symbol: str | None
    exchange: str | None
    quantity: float
    price: float
    fees: float
    amount: float
    notes: str | None
    source: str
    voided: bool
    void_reason: str | None


class VoidIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


def _pf(p: Portfolio) -> PortfolioOut:
    return PortfolioOut(id=p.id, name=p.name, base_currency=p.base_currency, is_paper=p.is_paper,
                        max_position_weight=p.max_position_weight, created_at=p.created_at)


def _tx(t: PortfolioTransaction) -> TxOut:
    return TxOut(id=t.id, seq=t.seq, trade_date=t.trade_date, type=t.type, symbol=t.symbol,
                 exchange=t.exchange, quantity=t.quantity, price=t.price, fees=t.fees,
                 amount=t.amount, notes=t.notes, source=t.source,
                 voided=t.voided_at is not None, void_reason=t.void_reason)


@router.post("", status_code=status.HTTP_201_CREATED)
def create(body: svc.PortfolioIn, p: WriterDep, session: SessionDep) -> PortfolioOut:
    return _pf(svc.create(session, p, body))


@router.get("")
def list_portfolios(p: PrincipalDep, session: SessionDep) -> list[PortfolioOut]:
    rows = session.scalars(select(Portfolio).where(Portfolio.workspace_id == p.workspace_id,
                                                   Portfolio.archived.is_(False))
                           .order_by(Portfolio.name))
    return [_pf(x) for x in rows]


@router.get("/{portfolio_id}")
def get_portfolio(portfolio_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> PortfolioOut:
    return _pf(svc.get(session, p, portfolio_id))


@router.delete("/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive(portfolio_id: uuid.UUID, p: WriterDep, session: SessionDep) -> None:
    svc.get(session, p, portfolio_id).archived = True  # the ledger is kept


@router.get("/{portfolio_id}/transactions")
def transactions(portfolio_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> list[TxOut]:
    pf = svc.get(session, p, portfolio_id)
    rows = session.scalars(select(PortfolioTransaction).where(
        PortfolioTransaction.portfolio_id == pf.id).order_by(
        PortfolioTransaction.trade_date, PortfolioTransaction.seq))
    return [_tx(t) for t in rows]


@router.post("/{portfolio_id}/transactions", status_code=status.HTTP_201_CREATED)
def add_transactions(portfolio_id: uuid.UUID, body: list[svc.TxIn], request: Request,
                     p: WriterDep, session: SessionDep, lake: LakeDep) -> list[TxOut]:
    """All-or-nothing; the book is replayed first (e.g. a sell beyond the holding is refused).
    ``external_ref`` makes re-sends idempotent."""
    pf = svc.get(session, p, portfolio_id)
    return [_tx(t) for t in svc.add_transactions(session, p, lake, pf, body,
                                                 request_id=request_id(request))]


@router.post("/{portfolio_id}/transactions/{tx_id}/void")
def void(portfolio_id: uuid.UUID, tx_id: uuid.UUID, body: VoidIn, p: WriterDep,
         session: SessionDep, lake: LakeDep) -> TxOut:
    pf = svc.get(session, p, portfolio_id)
    return _tx(svc.void(session, p, lake, pf, tx_id, body.reason))


@router.post("/{portfolio_id}/holdings/import", status_code=status.HTTP_201_CREATED)
async def import_holdings(portfolio_id: uuid.UUID, p: WriterDep, session: SessionDep,
                          lake: LakeDep, file: Annotated[UploadFile, File()],
                          trade_date: Annotated[date, Form()]) -> list[TxOut]:
    """CSV template: symbol,quantity,average_cost[,exchange]."""
    pf = svc.get(session, p, portfolio_id)
    data = await file.read(usvc.MAX_CSV_BYTES + 1)
    return [_tx(t) for t in svc.import_holdings_csv(session, p, lake, pf, data, trade_date)]


@router.get("/{portfolio_id}/holdings")
def holdings(portfolio_id: uuid.UUID, p: PrincipalDep, session: SessionDep, lake: LakeDep,
             as_of: date | None = None) -> dict[str, Any]:
    return svc.holdings(session, p, lake, svc.get(session, p, portfolio_id), as_of)


@router.get("/{portfolio_id}/risk")
def risk(portfolio_id: uuid.UUID, p: PrincipalDep, session: SessionDep, lake: LakeDep,
         as_of: date | None = None) -> dict[str, Any]:
    return svc.risk(session, p, lake, svc.get(session, p, portfolio_id), as_of)


@router.get("/{portfolio_id}/review")
def review(portfolio_id: uuid.UUID, p: PrincipalDep, session: SessionDep,
           lake: LakeDep) -> dict[str, Any]:
    return svc.review(session, p, lake, svc.get(session, p, portfolio_id))


@router.post("/{portfolio_id}/review/scan", status_code=status.HTTP_202_ACCEPTED)
def review_scan(portfolio_id: uuid.UUID, request: Request, p: WriterDep, session: SessionDep,
                lake: LakeDep, reference_index: str | None = "NIFTY 500",
                idempotency_key: Annotated[str | None, Header(max_length=80)] = None
                ) -> JSONResponse:
    """Scan the holdings (ranked against ``reference_index``) and assign holding states."""
    pf = svc.get(session, p, portfolio_id)
    name = f"Holdings: {pf.name}"
    u = session.scalar(select(Universe).where(Universe.workspace_id == p.workspace_id,
                                              Universe.name == name,
                                              Universe.kind == UniverseKind.HOLDINGS.value))
    if u is None or u.params.get("reference_index") != reference_index:
        u = usvc.create(session, p, usvc.UniverseIn(
            name=name, kind=UniverseKind.HOLDINGS, portfolio_id=pf.id,
            reference_index=reference_index), request_id(request))
    snap = usvc.resolve(session, p, lake, u.id, usvc.SnapshotIn(), request_id(request))
    h = svc.holdings(session, p, lake, pf)
    weights = {r["instrument_id"]: r["weight"] or 0.0 for r in h["positions"]}
    job, created = queue_scan(session, p, lake, snap, None, {}, idempotency_key,
                              request_id(request),
                              {"portfolio_id": str(pf.id), "weights": weights,
                               "max_position_weight": pf.max_position_weight})
    out = _job(job).model_dump(mode="json")
    return JSONResponse(out, status_code=202 if created else 200,
                        headers={"Location": out["status_url"]})

"""/api/v1/paper: simulated orders on PAPER portfolios, risk policies, reconciliation."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from quantera.api.deps import PrincipalDep, SessionDep, WriterDep
from quantera.api.routes.universes import LakeDep
from quantera.core.errors import NotFound
from quantera.paper import service as svc
from quantera.paper.models import PaperOrder

router = APIRouter(prefix="/paper-portfolios", tags=["paper trading"])


class NoteIn(BaseModel):
    note: str = Field(min_length=3, max_length=300)


@router.post("/{portfolio_id}/orders", status_code=status.HTTP_201_CREATED)
def place(portfolio_id: uuid.UUID, body: svc.OrderIn, p: WriterDep, session: SessionDep,
          lake: LakeDep) -> dict[str, Any]:
    """Simulated order. Rejected (with a reason) when it would short, overspend cash, breach the
    position limit, or while the portfolio is RISK_OFF. Never sent to a broker."""
    return svc.order_out(session, svc.place(session, p, lake, portfolio_id, body), True)


@router.get("/{portfolio_id}/orders")
def orders(portfolio_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> list[dict[str, Any]]:
    pf = svc._paper(session, p, portfolio_id)
    return [svc.order_out(session, o) for o in session.scalars(select(PaperOrder).where(
        PaperOrder.portfolio_id == pf.id).order_by(PaperOrder.created_at.desc()))]


@router.get("/{portfolio_id}/orders/{order_id}")
def order(portfolio_id: uuid.UUID, order_id: uuid.UUID, p: PrincipalDep,
          session: SessionDep) -> dict[str, Any]:
    pf = svc._paper(session, p, portfolio_id)
    o = session.get(PaperOrder, order_id)
    if o is None or o.portfolio_id != pf.id:
        raise NotFound("Order not found")
    return svc.order_out(session, o, True)


@router.post("/{portfolio_id}/orders/{order_id}/cancel")
def cancel(portfolio_id: uuid.UUID, order_id: uuid.UUID, p: WriterDep,
           session: SessionDep) -> dict[str, Any]:
    return svc.order_out(session, svc.cancel(session, p, portfolio_id, order_id), True)


@router.post("/{portfolio_id}/simulate")
def simulate(portfolio_id: uuid.UUID, p: WriterDep, session: SessionDep,
             lake: LakeDep) -> dict[str, int]:
    return svc.simulate(session, p, lake, portfolio_id)


@router.get("/{portfolio_id}/reconcile")
def reconcile(portfolio_id: uuid.UUID, p: PrincipalDep, session: SessionDep,
              lake: LakeDep) -> dict[str, Any]:
    return svc.reconcile(session, p, lake, portfolio_id)


@router.put("/{portfolio_id}/risk-policy")
def put_policy(portfolio_id: uuid.UUID, body: svc.PolicyIn, p: WriterDep,
               session: SessionDep) -> dict[str, Any]:
    pol = svc.set_policy(session, p, portfolio_id, body)
    return {"state": pol.state, **body.model_dump()}


@router.post("/{portfolio_id}/risk-policy/check")
def check(portfolio_id: uuid.UUID, p: WriterDep, session: SessionDep,
          lake: LakeDep) -> dict[str, Any]:
    return svc.check_policy(session, p, lake, portfolio_id)


@router.post("/{portfolio_id}/risk-policy/reset")
def reset(portfolio_id: uuid.UUID, body: NoteIn, p: WriterDep,
          session: SessionDep) -> dict[str, Any]:
    pol = svc.reset_policy(session, p, portfolio_id, body.note)
    return {"state": pol.state}

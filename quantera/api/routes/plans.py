"""/api/v1/trade-plans: price-level preview, saved plans, lifecycle, evaluation, alerts."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from quantera.api.deps import PrincipalDep, SessionDep, WriterDep
from quantera.api.routes.universes import LakeDep
from quantera.plans import service as svc
from quantera.plans.models import TradePlan, TradePlanEvent

router = APIRouter(prefix="/trade-plans", tags=["trade plans"])


@router.post("/preview")
def preview(body: svc.PreviewIn, p: PrincipalDep, lake: LakeDep) -> dict[str, Any]:
    """Exactly four price levels (Current Price, Stop-Loss, Target Price, Trailing Stop-Loss)
    with their states; ``explain`` says how each was calculated. No quantity or money."""
    return svc.preview(lake, body)


@router.post("", status_code=status.HTTP_201_CREATED)
def create(body: svc.PlanIn, p: WriterDep, session: SessionDep, lake: LakeDep) -> dict[str, Any]:
    return svc.out(session, svc.create(session, p, lake, body), events=True)


@router.get("")
def list_plans(p: PrincipalDep, session: SessionDep,
               status_: Annotated[str | None, Query(alias="status")] = None
               ) -> list[dict[str, Any]]:
    q = select(TradePlan).where(TradePlan.workspace_id == p.workspace_id)
    if status_:
        q = q.where(TradePlan.status == status_.upper())
    return [svc.out(session, x) for x in session.scalars(q.order_by(TradePlan.created_at.desc()))]


@router.get("/alerts")
def alerts(p: PrincipalDep, session: SessionDep,
           limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict[str, Any]]:
    rows = session.execute(select(TradePlanEvent, TradePlan.symbol).join(
        TradePlan, TradePlan.id == TradePlanEvent.plan_id).where(
        TradePlanEvent.workspace_id == p.workspace_id, TradePlanEvent.event.in_(svc.ALERTS))
        .order_by(TradePlanEvent.created_at.desc()).limit(limit)).all()
    return [{"plan_id": str(e.plan_id), "symbol": sym, "event": e.event, "rule": e.rule,
             "bar_date": e.bar_date.isoformat() if e.bar_date else None, "price": e.price,
             "details": e.details} for e, sym in rows]


@router.post("/evaluate")
def evaluate_all(p: WriterDep, session: SessionDep, lake: LakeDep) -> dict[str, int]:
    return svc.evaluate_all(session, lake, p.workspace_id)


@router.get("/{plan_id}")
def get_plan(plan_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> dict[str, Any]:
    return svc.out(session, svc.get(session, p, plan_id), events=True)


@router.patch("/{plan_id}")
def edit(plan_id: uuid.UUID, body: svc.EditIn, p: WriterDep, session: SessionDep,
         lake: LakeDep) -> dict[str, Any]:
    return svc.out(session, svc.edit(session, p, lake, svc.get(session, p, plan_id), body), True)


@router.post("/{plan_id}/{action}")
def action(plan_id: uuid.UUID, action: Literal["activate", "pause", "resume", "cancel",
                                               "evaluate"],
           body: svc.ActionIn, p: WriterDep, session: SessionDep, lake: LakeDep) -> dict[str, Any]:
    plan = svc.get(session, p, plan_id)
    if action == "activate":
        svc.activate(session, p, lake, plan, body)
    elif action == "pause":
        svc.pause(session, p, plan, body)
    elif action == "resume":
        svc.resume(session, p, lake, plan, body)
    elif action == "cancel":
        svc.cancel(session, p, plan, body)
    else:
        svc.evaluate(session, lake, plan)
    return svc.out(session, plan, events=True)

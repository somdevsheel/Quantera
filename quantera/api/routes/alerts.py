"""/api/v1/alerts: rules, evaluation, the combined feed (rules + trade plans), acknowledgement."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from quantera.alerts import service as svc
from quantera.alerts.models import AlertEvent, AlertRule
from quantera.api.deps import PrincipalDep, SessionDep, WriterDep
from quantera.api.routes.universes import LakeDep
from quantera.plans import service as plans
from quantera.plans.models import TradePlan, TradePlanEvent

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _rule(r: AlertRule) -> dict[str, Any]:
    return {"id": str(r.id), "name": r.name, "kind": r.kind, "symbol": r.symbol,
            "exchange": r.exchange, "threshold": r.threshold, "cooldown_days": r.cooldown_days,
            "channels": r.channels, "enabled": r.enabled,
            "last_triggered_on": r.last_triggered_on.isoformat() if r.last_triggered_on else None}


@router.post("/rules", status_code=status.HTTP_201_CREATED)
def create(body: svc.RuleIn, p: WriterDep, session: SessionDep, lake: LakeDep) -> dict[str, Any]:
    return _rule(svc.create(session, p, lake, body))


@router.get("/rules")
def rules(p: PrincipalDep, session: SessionDep) -> list[dict[str, Any]]:
    return [_rule(r) for r in session.scalars(select(AlertRule).where(
        AlertRule.workspace_id == p.workspace_id).order_by(AlertRule.created_at))]


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(rule_id: uuid.UUID, p: WriterDep, session: SessionDep) -> None:
    session.delete(svc.get(session, p, rule_id))


@router.post("/evaluate")
def evaluate(p: WriterDep, session: SessionDep, lake: LakeDep) -> dict[str, int]:
    return svc.evaluate(session, lake, p.workspace_id)


@router.get("/feed")
def feed(p: PrincipalDep, session: SessionDep,
         unacknowledged: bool = False,
         limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict[str, Any]]:
    """Rule alerts and trade-plan alerts (fills, approaching levels, reviews), newest first."""
    q = select(AlertEvent, AlertRule).join(AlertRule, AlertRule.id == AlertEvent.rule_id).where(
        AlertEvent.workspace_id == p.workspace_id)
    if unacknowledged:
        q = q.where(AlertEvent.acknowledged_at.is_(None))
    out = [{"id": str(e.id), "source": "rule", "kind": r.kind, "symbol": r.symbol,
            "bar_date": e.bar_date.isoformat(), "message": e.message, "value": e.value,
            "route": e.details.get("route"), "acknowledged": e.acknowledged_at is not None,
            "created_at": e.created_at.isoformat()}
           for e, r in session.execute(q.order_by(AlertEvent.created_at.desc()).limit(limit))]
    pq = select(TradePlanEvent, TradePlan).join(TradePlan, TradePlan.id == TradePlanEvent.plan_id
                                               ).where(TradePlanEvent.workspace_id ==
                                                       p.workspace_id,
                                                       TradePlanEvent.event.in_(plans.ALERTS))
    out += [{"id": str(e.id), "source": "trade_plan", "kind": e.event, "symbol": t.symbol,
             "bar_date": e.bar_date.isoformat() if e.bar_date else None,
             "message": f"{t.symbol} {e.event.lower().replace('_', ' ')} ({e.rule})",
             "value": e.price, "route": f"/stocks/{t.exchange}/{t.symbol}",
             "acknowledged": None, "created_at": e.created_at.isoformat()}
            for e, t in session.execute(pq.order_by(TradePlanEvent.created_at.desc())
                                        .limit(limit))]
    out.sort(key=lambda x: str(x["created_at"]), reverse=True)
    return out[:limit]


@router.post("/{event_id}/acknowledge")
def acknowledge(event_id: uuid.UUID, p: WriterDep, session: SessionDep) -> dict[str, Any]:
    e = svc.acknowledge(session, p, event_id)
    return {"id": str(e.id), "acknowledged_at": e.acknowledged_at}

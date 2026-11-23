"""Alert rules evaluated on the latest daily bar (deduplicated per rule and bar; cooldown).

Kinds (threshold meaning):
  PRICE_ABOVE / PRICE_BELOW   close crosses the level (INR)
  MOVE_1D                     |1-day change| >= threshold (fraction, e.g. 0.05)
  VOLUME_SPIKE                volume >= threshold x 20-day median volume
  RSI_ABOVE / RSI_BELOW       RSI(14) above / below the level
  DRAWDOWN_52W                close <= (1 - threshold) x 52-week high
  DECISION_CHANGE             the latest scan state differs from the previous one (threshold
                              unused)
Delivery: IN_APP (stored here). Push to the Android app is opt-in on the device and shows
only the symbol and rule name on a locked screen, never portfolio details.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from quant import indicators as ind
from quantera.alerts.models import AlertEvent, AlertRule
from quantera.analysis.models import DecisionRecord
from quantera.analysis.scan import latest_market_date
from quantera.core.errors import NotFound
from quantera.identity.service import Principal
from quantera.lake.client import LakeClient
from quantera.research.service import resolve as resolve_listing

Channel = Literal["IN_APP", "PUSH"]
Kind = Literal["PRICE_ABOVE", "PRICE_BELOW", "MOVE_1D", "VOLUME_SPIKE", "RSI_ABOVE",
               "RSI_BELOW", "DRAWDOWN_52W", "DECISION_CHANGE"]


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Kind
    symbol: str = Field(min_length=1, max_length=40)
    exchange: str = "NSE"
    threshold: float = Field(default=0.0, ge=0)
    cooldown_days: int = Field(default=1, ge=0, le=365)
    channels: list[Channel] = Field(default_factory=lambda: list[Channel](["IN_APP"]))


def create(session: Session, p: Principal, lake: LakeClient, body: RuleIn) -> AlertRule:
    inst = resolve_listing(lake, body.exchange, body.symbol)
    r = AlertRule(workspace_id=p.workspace_id, created_by=p.user_id, name=body.name,
                  kind=body.kind, instrument_id=inst["instrument_id"], symbol=inst["symbol"],
                  exchange=inst["exchange_mic"], threshold=body.threshold,
                  cooldown_days=body.cooldown_days, channels=list(body.channels))
    session.add(r)
    session.flush()
    return r


def get(session: Session, p: Principal, rid: uuid.UUID) -> AlertRule:
    r = session.get(AlertRule, rid)
    if r is None or r.workspace_id != p.workspace_id:
        raise NotFound("Alert rule not found")
    return r


def _check(session: Session, r: AlertRule, rows: list[dict[str, Any]]
           ) -> tuple[bool, float | None, str]:
    c = np.array([float(x["close"]) for x in rows])
    last = c[-1]
    t = r.threshold
    if r.kind == "PRICE_ABOVE":
        return last >= t, float(last), f"{r.symbol} closed at {last:.2f}, at or above {t:.2f}"
    if r.kind == "PRICE_BELOW":
        return last <= t, float(last), f"{r.symbol} closed at {last:.2f}, at or below {t:.2f}"
    if r.kind == "MOVE_1D" and len(c) > 1:
        ch = float(last / c[-2] - 1)
        return abs(ch) >= t, ch, f"{r.symbol} moved {ch:+.1%} in a day"
    if r.kind == "VOLUME_SPIKE" and len(rows) > 21:
        v = np.array([float(x.get("volume") or 0) for x in rows])
        med = float(np.median(v[-21:-1]))
        ratio = float(v[-1] / med) if med > 0 else 0.0
        return ratio >= t, ratio, f"{r.symbol} volume {ratio:.1f}x its 20-day median"
    if r.kind in ("RSI_ABOVE", "RSI_BELOW"):
        rsi = float(ind.rsi(c)[-1])
        if not np.isfinite(rsi):
            return False, None, ""
        hit = rsi >= t if r.kind == "RSI_ABOVE" else rsi <= t
        return hit, rsi, f"{r.symbol} RSI(14) {rsi:.0f}"
    if r.kind == "DRAWDOWN_52W" and len(c) > 20:
        dd = float(last / c[-252:].max() - 1)
        return dd <= -t, dd, f"{r.symbol} is {dd:.1%} below its 52-week high"
    if r.kind == "DECISION_CHANGE":
        recs = list(session.scalars(select(DecisionRecord).where(
            DecisionRecord.workspace_id == r.workspace_id,
            DecisionRecord.instrument_id == r.instrument_id)
            .order_by(DecisionRecord.as_of.desc(), DecisionRecord.created_at.desc()).limit(2)))
        if len(recs) == 2 and recs[0].state != recs[1].state:
            return True, None, f"{r.symbol}: {recs[1].state} -> {recs[0].state}"
        return False, None, ""
    return False, None, ""


def evaluate(session: Session, lake: LakeClient, workspace_id: uuid.UUID | None = None
             ) -> dict[str, int]:
    latest = latest_market_date(lake, date.today())
    q = select(AlertRule).where(AlertRule.enabled.is_(True))
    if workspace_id:
        q = q.where(AlertRule.workspace_id == workspace_id)
    triggered = 0
    rules = list(session.scalars(q))
    for r in rules:
        _, rows = lake.prices_daily(instrument_id=r.instrument_id, exchange=r.exchange,
                                    start=latest - timedelta(days=400), end=latest,
                                    adjust="split", as_of=latest)
        rows = [x for x in rows if x.get("close")]
        if not rows:
            continue
        bar = date.fromisoformat(str(rows[-1]["trade_date"])[:10])
        if r.last_triggered_on and (bar - r.last_triggered_on).days < max(r.cooldown_days, 1):
            continue
        hit, val, msg = _check(session, r, rows)
        if not hit or session.scalar(select(AlertEvent.id).where(
                AlertEvent.rule_id == r.id, AlertEvent.bar_date == bar)):
            continue
        session.add(AlertEvent(workspace_id=r.workspace_id, rule_id=r.id, bar_date=bar,
                               value=val, message=msg[:300],
                               details={"kind": r.kind, "threshold": r.threshold,
                                        "route": f"/stocks/{r.exchange}/{r.symbol}"},
                               delivery={ch: "STORED" if ch == "IN_APP" else "QUEUED"
                                         for ch in r.channels}))
        r.last_triggered_on = bar
        triggered += 1
    return {"rules": len(rules), "triggered": triggered}


def acknowledge(session: Session, p: Principal, eid: uuid.UUID) -> AlertEvent:
    e = session.get(AlertEvent, eid)
    if e is None or e.workspace_id != p.workspace_id:
        raise NotFound("Alert not found")
    e.acknowledged_at = e.acknowledged_at or datetime.now(UTC)
    return e

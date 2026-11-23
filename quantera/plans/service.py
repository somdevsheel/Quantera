"""Trade plans: preview (4-field price panel), save, edit, activate/pause/resume/cancel,
bar-by-bar evaluation with the protection engine, paper booking, alert feed.

Rules: risk-increasing changes (stop farther away, wider/removed trail, pausing or cancelling
protection on an open position) need ``confirm_risk_increase``; every change carries the
plan version (optimistic concurrency) and is recorded and audited; evaluation is keyed by
bar, so duplicate or delayed runs change nothing; paused bars are skipped, never back-filled;
a split/bonus/merger/delisting ex-date pauses the plan for review. Paper booking is LONG-only
into PAPER portfolios (a cash short needs SLB, which is not modelled). Nothing reaches a broker.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quant import indicators as ind
from quant import protection as pr
from quantera.analysis.scan import latest_market_date
from quantera.core import audit
from quantera.core.errors import Conflict, DomainError, NotFound
from quantera.identity.service import Principal
from quantera.lake.client import LakeClient
from quantera.plans.models import TradePlan, TradePlanEvent
from quantera.portfolios import service as psvc
from quantera.research.service import resolve as resolve_listing

POLICY_VERSION = "trade_plan_v1"
OPEN = ("DRAFT", "ACTIVE", "PAUSED")
REVIEW_EVENTS = ("SPLIT", "BONUS", "RIGHTS", "MERGER", "SPINOFF", "DELISTING")
HISTORY_DAYS = 200


class StopIn(BaseModel):
    method: Literal["FIXED_PRICE", "PERCENT", "ATR", "SWING", "VOLATILITY"] = "ATR"
    value: float = Field(default=2.0, gt=0)


class TargetIn(BaseModel):
    method: Literal["FIXED_PRICE", "PERCENT", "ATR", "RISK_REWARD"] = "RISK_REWARD"
    value: float = Field(default=2.0, gt=0)
    fraction: float = Field(default=1.0, gt=0, le=1)


class TrailIn(BaseModel):
    method: Literal["AMOUNT", "PERCENT", "ATR"] = "ATR"
    value: float = Field(default=3.0, gt=0)
    activate_after_r: float = Field(default=0.0, ge=0, le=20)


class PreviewIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    exchange: str = "NSE"
    side: Literal["LONG", "SHORT"] = "LONG"
    entry_price: float | None = Field(default=None, gt=0)
    stop: StopIn = Field(default_factory=StopIn)
    targets: list[TargetIn] = Field(default_factory=lambda: [TargetIn()], max_length=5)
    trailing: TrailIn | None = Field(default_factory=TrailIn)
    stop_price: float | None = Field(default=None, gt=0)  # manual overrides
    target_prices: list[float] | None = Field(default=None, max_length=5)
    min_reward_risk: float | None = Field(default=None, gt=0)


class PlanIn(PreviewIn):
    client_ref: str | None = Field(default=None, min_length=1, max_length=64)
    portfolio_id: uuid.UUID | None = None
    quantity: float | None = Field(default=None, gt=0)
    book_entry: bool = False
    stop_order: Literal["STOP_MARKET", "STOP_LIMIT"] = "STOP_MARKET"
    stop_limit_offset: float | None = Field(default=None, gt=0)
    breakeven_after_first_target: bool = False
    time_stop_bars: int | None = Field(default=None, ge=1, le=2520)
    fee_bps: float = Field(default=3.0, ge=0, le=200)
    slippage_bps: float = Field(default=5.0, ge=0, le=500)
    activate: bool = False
    # Historical replay (paper research): the plan starts at this date and is evaluated
    # forward through real bars. Without it, a plan starts at the latest market date.
    as_of: date | None = None

    @model_validator(mode="after")
    def _check(self) -> PlanIn:
        if self.stop_order == "STOP_LIMIT" and self.stop_limit_offset is None:
            raise ValueError("a STOP_LIMIT needs stop_limit_offset")
        if self.book_entry and (self.portfolio_id is None or self.quantity is None):
            raise ValueError("book_entry needs portfolio_id and quantity")
        if self.book_entry and self.side == "SHORT":
            raise ValueError("short paper positions are not supported (no SLB model); a SHORT "
                             "plan can track levels without booking")
        return self


class EditIn(BaseModel):
    expected_version: int = Field(ge=1)
    stop_price: float | None = Field(default=None, gt=0)
    target_prices: list[float] | None = Field(default=None, max_length=5)
    trailing: TrailIn | None = None
    remove_trailing: bool = False
    confirm_risk_increase: bool = False
    note: str | None = Field(default=None, max_length=300)


class ActionIn(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)
    confirm_risk_increase: bool = False
    note: str | None = Field(default=None, max_length=300)


def _now() -> datetime:
    return datetime.now(UTC)


def _bars(lake: LakeClient, iid: str, mic: str, start: date, end: date) -> list[dict[str, Any]]:
    _, rows = lake.prices_daily(instrument_id=iid, exchange=mic, start=start, end=end,
                                adjust="none", as_of=end)  # traded prices, as orders see them
    return [r for r in rows if r.get("close")]


def _levels(lake: LakeClient, body: PreviewIn, as_of: date) -> dict[str, Any]:
    inst = resolve_listing(lake, body.exchange, body.symbol)
    rows = _bars(lake, inst["instrument_id"], inst["exchange_mic"],
                 as_of - timedelta(days=HISTORY_DAYS), as_of)
    if len(rows) < 30:
        raise DomainError(f"Not enough recent prices for {inst['symbol']}")
    o, h, lo, c = (np.array([float(r.get(k) or r["close"]) for r in rows])
                   for k in ("open", "high", "low", "close"))
    current = float(c[-1])
    ref = body.entry_price or current
    tick = 0.05 if inst["exchange_mic"] == "XNSE" else 0.01
    try:
        if body.stop_price:
            pr.check_side(body.side, ref, body.stop_price, "stop")
            stop: dict[str, Any] = {
                "price": pr.round_level(body.stop_price, tick, body.side, "stop"),
                "method": "MANUAL"}
        else:
            stop = pr.initial_stop(body.side, ref, o, h, lo, c, body.stop.method,
                                   body.stop.value, tick=tick)
        a = ind.atr(h, lo, c)
        atr_v = float(a[-1]) if math.isfinite(a[-1]) else None
        if body.target_prices is not None:
            cfgs = [{"method": "FIXED_PRICE", "value": x, "fraction": 1 / len(body.target_prices)}
                    for x in body.target_prices]
        else:
            cfgs = [t.model_dump() for t in body.targets]
        tps, warnings = pr.targets(body.side, ref, float(stop["price"]), cfgs, atr_v, tick,
                                   body.min_reward_risk)
    except ValueError as exc:
        raise DomainError(str(exc)) from exc
    return {"instrument": inst, "current": current, "ref": ref, "tick": tick, "stop": stop,
            "targets": tps, "warnings": warnings, "atr": atr_v,
            "as_of": str(rows[-1]["trade_date"])[:10]}


def preview(lake: LakeClient, body: PreviewIn) -> dict[str, Any]:
    lv = _levels(lake, body, latest_market_date(lake, date.today()))
    st = pr.PlanState(side=body.side, entry=lv["ref"], quantity=1.0,
                      initial_stop=lv["stop"]["price"], stop=lv["stop"]["price"],
                      targets=[pr.Leg(t["price"], t["fraction"]) for t in lv["targets"]],
                      trail=body.trailing.model_dump() if body.trailing else None, tick=lv["tick"])
    return {"price_levels": pr.price_panel(lv["current"], st, draft=True),
            "explain": {"side": body.side, "reference_price": lv["ref"], "timeframe": "1d",
                        "as_of": lv["as_of"], "tick_size": lv["tick"], "stop_loss": lv["stop"],
                        "targets": lv["targets"], "trailing": body.trailing.model_dump()
                        if body.trailing else None,
                        "trailing_note": "starts once price moves in favour; only tightens; "
                                         "applies from the next bar",
                        "warnings": lv["warnings"], "version": pr.VERSION},
            "disclaimer": "Suggested levels, not orders or advice. A stop is a trigger, not a "
                          "guaranteed price: gaps, circuit limits, halts and illiquidity can "
                          "cause worse fills or no fill, and stop-limit orders may not execute."}


# --------------------------------------------------------------------------- lifecycle
def get(session: Session, p: Principal, pid: uuid.UUID) -> TradePlan:
    plan = session.get(TradePlan, pid)
    if plan is None or plan.workspace_id != p.workspace_id:
        raise NotFound("Trade plan not found")
    return plan


def _event(session: Session, plan: TradePlan, event: str, *, key: str | None = None,
           actor: uuid.UUID | None = None, rule: str | None = None, bar_date: date | None = None,
           price: float | None = None, quantity: float | None = None,
           details: dict[str, Any] | None = None) -> TradePlanEvent | None:
    k = key or f"{event}:{uuid.uuid4().hex}"
    if key and session.scalar(select(TradePlanEvent.id).where(
            TradePlanEvent.plan_id == plan.id, TradePlanEvent.idempotency_key == k)):
        return None
    seq = (session.scalar(select(func.max(TradePlanEvent.seq)).where(
        TradePlanEvent.plan_id == plan.id)) or 0) + 1
    e = TradePlanEvent(workspace_id=plan.workspace_id, plan_id=plan.id, seq=seq, event=event,
                       rule=rule, bar_date=bar_date, price=price, quantity=quantity,
                       actor_user_id=actor, idempotency_key=k[:80], details=details or {})
    session.add(e)
    session.flush()
    return e


def _st(plan: TradePlan) -> pr.PlanState:
    return pr.PlanState.from_json(plan.state)


def _save(plan: TradePlan, st: pr.PlanState, bump: bool = True) -> None:
    plan.state = st.to_json()
    plan.updated_at = _now()
    if bump:
        plan.version += 1


def _version(plan: TradePlan, expected: int | None) -> None:
    if expected is not None and expected != plan.version:
        raise Conflict(f"The plan changed (version {plan.version}, you had {expected}); reload")


def create(session: Session, p: Principal, lake: LakeClient, body: PlanIn) -> TradePlan:
    ref = body.client_ref or uuid.uuid4().hex
    old = session.scalar(select(TradePlan).where(TradePlan.workspace_id == p.workspace_id,
                                                 TradePlan.client_ref == ref))
    if old is not None:
        if old.config.get("request") != body.model_dump(mode="json"):
            raise Conflict(f"client_ref {ref} was already used for a different plan")
        return old
    if body.portfolio_id:
        pf = psvc.get(session, p, body.portfolio_id)
        if body.book_entry and not pf.is_paper:
            raise DomainError("Trade plans book fills only into PAPER portfolios")
    latest = latest_market_date(lake, date.today())
    as_of = min(body.as_of, latest) if body.as_of else latest
    lv = _levels(lake, body, as_of)
    inst = lv["instrument"]
    st = pr.PlanState(side=body.side, entry=lv["ref"], quantity=body.quantity or 1.0,
                      initial_stop=lv["stop"]["price"], stop=lv["stop"]["price"],
                      targets=[pr.Leg(t["price"], t["fraction"]) for t in lv["targets"]],
                      stop_limit_offset=body.stop_limit_offset
                      if body.stop_order == "STOP_LIMIT" else None,
                      trail=body.trailing.model_dump() if body.trailing else None,
                      breakeven_after_first_target=body.breakeven_after_first_target,
                      fee_per_share=lv["ref"] * body.fee_bps / 1e4,
                      slippage_bps=body.slippage_bps, time_stop_bars=body.time_stop_bars,
                      tick=lv["tick"])
    plan = TradePlan(workspace_id=p.workspace_id, created_by=p.user_id, client_ref=ref,
                     portfolio_id=body.portfolio_id, instrument_id=inst["instrument_id"],
                     symbol=inst["symbol"], exchange=inst["exchange_mic"], side=body.side,
                     status="DRAFT", entry_price=lv["ref"],
                     entry_date=date.fromisoformat(lv["as_of"]), quantity=body.quantity or 1.0,
                     books_paper=body.book_entry,
                     config={"request": body.model_dump(mode="json"), "explain": {
                         "stop": lv["stop"], "targets": lv["targets"], "atr": lv["atr"]},
                         "fee_bps": body.fee_bps, "warnings": lv["warnings"]},
                     state=st.to_json(), last_price=lv["current"], version=1,
                     policy_version=POLICY_VERSION, updated_at=_now())
    session.add(plan)
    session.flush()
    _event(session, plan, "CREATED", actor=p.user_id,
           details={"stop": st.stop, "targets": [t.price for t in st.targets]})
    audit.record(session, "trade_plan.create", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="trade_plan", resource_id=plan.id)
    if body.activate:
        activate(session, p, lake, plan, ActionIn())
    return plan


def activate(session: Session, p: Principal, lake: LakeClient, plan: TradePlan,
             body: ActionIn) -> TradePlan:
    if plan.status != "DRAFT":
        raise Conflict(f"Only a DRAFT plan can be activated ({plan.status})")
    _version(plan, body.expected_version)
    st = _st(plan)
    replay = bool(plan.config.get("request", {}).get("as_of"))
    last_day = plan.entry_date if replay else latest_market_date(lake, date.today())
    rows = _bars(lake, plan.instrument_id, plan.exchange, last_day - timedelta(days=10), last_day)
    last = float(rows[-1]["close"])
    try:
        pr.validate_levels(st, last)
    except ValueError as exc:
        raise DomainError(f"Cannot activate: {exc}. Edit the levels first.") from exc
    if plan.books_paper and plan.portfolio_id and not plan.config.get("entry_booked"):
        pf = psvc.get(session, p, plan.portfolio_id)
        psvc.add_transactions(session, p, lake, pf, [psvc.TxIn(
            trade_date=plan.entry_date, type="BUY", symbol=plan.symbol,
            exchange=plan.exchange, quantity=plan.quantity, price=plan.entry_price,
            fees=plan.quantity * plan.entry_price * plan.config.get("fee_bps", 0) / 1e4,
            external_ref=f"plan:{plan.id}:entry")], source="trade_plan")
        plan.config = {**plan.config, "entry_booked": True}
        _event(session, plan, "ENTRY_BOOKED", actor=p.user_id, bar_date=plan.entry_date,
               price=plan.entry_price, quantity=plan.quantity)
    last_bar = date.fromisoformat(str(rows[-1]["trade_date"])[:10])
    plan.evaluated_through = max(plan.entry_date, last_bar)  # never back-evaluate
    plan.status, plan.last_price = "ACTIVE", last
    _save(plan, st)
    _event(session, plan, "ACTIVATED", actor=p.user_id, details={"note": body.note})
    audit.record(session, "trade_plan.activate", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="trade_plan", resource_id=plan.id)
    return plan


def pause(session: Session, p: Principal, plan: TradePlan, body: ActionIn) -> TradePlan:
    if plan.status != "ACTIVE":
        raise Conflict(f"Only an ACTIVE plan can be paused ({plan.status})")
    _version(plan, body.expected_version)
    if not body.confirm_risk_increase:
        raise Conflict("Pausing removes protection from an open position; confirm with "
                       "confirm_risk_increase=true")
    plan.status = "PAUSED"
    _save(plan, _st(plan))
    _event(session, plan, "PAUSED", actor=p.user_id, details={"note": body.note})
    audit.record(session, "trade_plan.pause", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="trade_plan", resource_id=plan.id)
    return plan


def resume(session: Session, p: Principal, lake: LakeClient, plan: TradePlan,
           body: ActionIn) -> TradePlan:
    if plan.status != "PAUSED":
        raise Conflict(f"Only a PAUSED plan can be resumed ({plan.status})")
    _version(plan, body.expected_version)
    last_day = latest_market_date(lake, date.today())
    skipped_from = plan.evaluated_through
    plan.evaluated_through = max(plan.evaluated_through or plan.entry_date, last_day)
    plan.status = "ACTIVE"
    _save(plan, _st(plan))
    _event(session, plan, "RESUMED", actor=p.user_id,
           details={"skipped_after": skipped_from.isoformat() if skipped_from else None,
                    "skipped_through": last_day.isoformat(), "note": body.note})
    return plan


def cancel(session: Session, p: Principal, plan: TradePlan, body: ActionIn) -> TradePlan:
    if plan.status not in OPEN:
        raise Conflict(f"A {plan.status} plan cannot be cancelled")
    _version(plan, body.expected_version)
    st = _st(plan)
    if plan.status != "DRAFT" and st.remaining > 1e-12 and not body.confirm_risk_increase:
        raise Conflict("Cancelling removes protection from an open position; confirm with "
                       "confirm_risk_increase=true")
    if st.stop_state in ("ACTIVE", "PENDING"):
        st.stop_state = "CANCELLED"
    for t in st.targets:
        if t.state == "ACTIVE":
            t.state = "CANCELLED"
    st.resting_limit = None
    plan.status, plan.closed_at = "CANCELLED", _now()
    _save(plan, st)
    _event(session, plan, "CANCELLED", actor=p.user_id, details={"note": body.note})
    audit.record(session, "trade_plan.cancel", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="trade_plan", resource_id=plan.id)
    return plan


def edit(session: Session, p: Principal, lake: LakeClient, plan: TradePlan,
         body: EditIn) -> TradePlan:
    if plan.status not in OPEN:
        raise Conflict(f"A {plan.status} plan cannot be edited")
    _version(plan, body.expected_version)
    st = _st(plan)
    before = st.to_json()
    new_stop = (pr.round_level(body.stop_price, st.tick, st.side, "stop")
                if body.stop_price else None)
    new_trail: dict[str, Any] | None = (
        {} if body.remove_trailing else body.trailing.model_dump() if body.trailing else None)
    reasons = pr.is_risk_increase(st, new_stop, new_trail)
    if reasons and not body.confirm_risk_increase:
        raise Conflict("This edit increases risk (" + "; ".join(reasons) + "); confirm with "
                       "confirm_risk_increase=true")
    if new_stop is not None:
        st.stop = new_stop
        if plan.status == "DRAFT":
            st.initial_stop = new_stop
    if body.target_prices is not None:
        done = [t for t in st.targets if t.state != "ACTIVE"]
        left = 1.0 - sum(t.fraction for t in done)
        fresh = sorted((pr.Leg(pr.round_level(x, st.tick, st.side, "target"),
                               left / len(body.target_prices)) for x in body.target_prices),
                       key=lambda t: st.s * t.price)
        st.targets = done + fresh
    if new_trail is not None:
        st.trail = new_trail or None
        if not new_trail:
            st.trail_level = None
    last_day = latest_market_date(lake, date.today())
    rows = _bars(lake, plan.instrument_id, plan.exchange, last_day - timedelta(days=10), last_day)
    try:
        pr.validate_levels(st, float(rows[-1]["close"]))
    except ValueError as exc:
        raise DomainError(str(exc)) from exc
    _save(plan, st)
    after = st.to_json()
    _event(session, plan, "EDITED", actor=p.user_id,
           details={"changes": {k: {"before": before[k], "after": after[k]} for k in after
                                if before.get(k) != after[k]},
                    "risk_increase": reasons, "note": body.note, "effective": "next bar"})
    audit.record(session, "trade_plan.edit", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="trade_plan", resource_id=plan.id,
                 details={"risk_increase": reasons})
    return plan


def evaluate(session: Session, lake: LakeClient, plan: TradePlan,
             through: date | None = None) -> int:
    """Feed every new bar to the engine; returns the number of new events."""
    if plan.status != "ACTIVE":
        return 0
    end = through or latest_market_date(lake, date.today())
    rows = _bars(lake, plan.instrument_id, plan.exchange, plan.entry_date - timedelta(days=60),
                 end)
    if not rows:
        return 0
    st = _st(plan)
    a = ind.atr([float(r.get("high") or r["close"]) for r in rows],
                [float(r.get("low") or r["close"]) for r in rows],
                [float(r["close"]) for r in rows])
    acts = [x for x in lake.corporate_actions(plan.instrument_id, plan.exchange, end)
            if x.get("event") in REVIEW_EVENTS]
    done = plan.evaluated_through or plan.entry_date
    n = 0
    for i, r in enumerate(rows):
        d = date.fromisoformat(str(r["trade_date"])[:10])
        if d <= done:
            continue
        hit = [x for x in acts if done < date.fromisoformat(str(x["ex_date"])[:10]) <= d]
        if hit:
            plan.status = "PAUSED"
            if _event(session, plan, "CORPORATE_ACTION_REVIEW", key=f"CA:{hit[0]['ex_date']}",
                      bar_date=d, details={"action": hit[0].get("event"),
                                           "ex_date": str(hit[0]["ex_date"])[:10],
                                           "note": "levels are in pre-action prices; edit them, "
                                                   "then resume"}):
                n += 1
            break
        bar = pr.Bar(d, float(r.get("open") or r["close"]), float(r.get("high") or r["close"]),
                     float(r.get("low") or r["close"]), float(r["close"]))
        atr_v = float(a[i]) if math.isfinite(a[i]) else None
        for e in pr.step(st, bar, atr_v):
            rec = _event(session, plan, e.kind, key=e.key, rule=e.rule, bar_date=e.day,
                         price=e.price, quantity=e.quantity, details=e.details)
            if rec is None:
                continue
            n += 1
            if e.kind == "FILL" and plan.books_paper and plan.portfolio_id and e.quantity \
                    and plan.side == "LONG":
                pf_owner = _owner(plan)
                pf = psvc.get(session, pf_owner, plan.portfolio_id)
                txs = psvc.add_transactions(session, pf_owner, lake, pf, [psvc.TxIn(
                    trade_date=e.day, type="SELL", symbol=plan.symbol, exchange=plan.exchange,
                    quantity=e.quantity, price=e.price or bar.close,
                    fees=e.quantity * (e.price or bar.close) * plan.config.get("fee_bps", 0)
                    / 1e4, external_ref=f"plan:{plan.id}:{e.key}")], source="trade_plan")
                rec.details = {**rec.details, "transaction_ids": [str(t.id) for t in txs]}
        plan.evaluated_through, plan.last_price = d, bar.close
        if st.closed:
            plan.status, plan.closed_at = "CLOSED", _now()
            break
    _save(plan, st, bump=n > 0)
    return n


def _owner(plan: TradePlan) -> Principal:
    from quantera.identity.models import Role

    return Principal(plan.created_by, plan.workspace_id, uuid.uuid4(), Role.OWNER)


def evaluate_all(session: Session, lake: LakeClient,
                 workspace_id: uuid.UUID | None = None) -> dict[str, int]:
    q = select(TradePlan).where(TradePlan.status == "ACTIVE")
    if workspace_id:
        q = q.where(TradePlan.workspace_id == workspace_id)
    plans = list(session.scalars(q))
    return {"plans": len(plans), "events": sum(evaluate(session, lake, x) for x in plans)}


ALERTS = ("FILL", "APPROACHING", "STOP_LIMIT_NOT_FILLED", "CORPORATE_ACTION_REVIEW",
          "STOP_MOVED", "AMBIGUOUS_BAR")


def out(session: Session, plan: TradePlan, events: bool = False) -> dict[str, Any]:
    st = _st(plan)
    o: dict[str, Any] = {
        "id": str(plan.id), "client_ref": plan.client_ref, "status": plan.status,
        "version": plan.version, "side": plan.side, "symbol": plan.symbol,
        "exchange": plan.exchange, "route": f"/stocks/{plan.exchange}/{plan.symbol}",
        "price_levels": pr.price_panel(plan.last_price or plan.entry_price, st,
                                       draft=plan.status == "DRAFT",
                                       paused=plan.status == "PAUSED"),
        "legs": {"stop": {"price": st.stop, "state": st.stop_state,
                          "order": "STOP_LIMIT" if st.stop_limit_offset else "STOP_MARKET",
                          "resting_limit": st.resting_limit},
                 "targets": [{"price": t.price, "fraction": t.fraction, "state": t.state}
                             for t in st.targets],
                 "trailing": {"config": st.trail, "level": st.trail_level}},
        "position": {"quantity": plan.quantity, "remaining": st.remaining,
                     "entry_price": plan.entry_price, "entry_date": plan.entry_date.isoformat(),
                     "portfolio_id": str(plan.portfolio_id) if plan.portfolio_id else None,
                     "books_paper": plan.books_paper},
        "evaluated_through": plan.evaluated_through.isoformat() if plan.evaluated_through
        else None,
        "warnings": plan.config.get("warnings", []), "engine_version": pr.VERSION,
        "disclaimer": "Paper simulation only; no live orders. A stop is a trigger, not a "
                      "guaranteed price."}
    if events:
        o["events"] = [{"seq": e.seq, "event": e.event, "rule": e.rule,
                        "bar_date": e.bar_date.isoformat() if e.bar_date else None,
                        "price": e.price, "quantity": e.quantity, "details": e.details,
                        "at": e.created_at.isoformat()}
                       for e in session.scalars(select(TradePlanEvent).where(
                           TradePlanEvent.plan_id == plan.id).order_by(TradePlanEvent.seq))]
    return o

"""Paper trading: orders against PAPER portfolios only, simulated on daily bars, fills booked
into the portfolio ledger (the single book of cash and positions), risk policies, and
reconciliation. Live execution does not exist (QUANTERA_ENABLE_LIVE_TRADING can only be
false).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quant import backtest as bt
from quant import paper as pp
from quantera.analysis.scan import latest_market_date
from quantera.core import audit
from quantera.core.errors import Conflict, DomainError, NotFound
from quantera.identity.service import Principal
from quantera.lake.client import LakeClient
from quantera.paper.models import PaperOrder, PaperOrderEvent, RiskBreach, RiskPolicy
from quantera.portfolios import service as psvc
from quantera.portfolios.models import Portfolio, PortfolioTransaction
from quantera.research.service import resolve as resolve_listing

COSTS = bt.Costs()


class OrderIn(BaseModel):
    client_order_id: str = Field(min_length=1, max_length=64)
    symbol: str = Field(min_length=1, max_length=40)
    exchange: str = "NSE"
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT", "STOP", "STOP_LIMIT"] = "MARKET"
    quantity: float = Field(gt=0)
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    tif: Literal["DAY", "GTC"] = "DAY"
    placed_on: date | None = None  # historical replay (research); default: latest market day

    @model_validator(mode="after")
    def _prices(self) -> OrderIn:
        if self.order_type in ("LIMIT", "STOP_LIMIT") and self.limit_price is None:
            raise ValueError(f"{self.order_type} needs limit_price")
        if self.order_type in ("STOP", "STOP_LIMIT") and self.stop_price is None:
            raise ValueError(f"{self.order_type} needs stop_price")
        return self


class PolicyIn(BaseModel):
    daily_loss_limit: float | None = Field(default=None, gt=0, le=1)
    max_drawdown: float | None = Field(default=None, gt=0, le=1)
    max_position_weight: float | None = Field(default=None, gt=0, le=1)


def _paper(session: Session, p: Principal, pid: uuid.UUID) -> Portfolio:
    pf = psvc.get(session, p, pid)
    if not pf.is_paper:
        raise DomainError("Orders are accepted only for PAPER portfolios; there is no live "
                          "execution")
    return pf


def _event(session: Session, o: PaperOrder, event: str, to: str | None,
           bar: date | None = None, details: dict[str, Any] | None = None) -> None:
    if to is not None and to != o.status and not pp.can_move(o.status, to):
        raise Conflict(f"Illegal transition {o.status} -> {to}")
    seq = (session.scalar(select(func.max(PaperOrderEvent.seq)).where(
        PaperOrderEvent.order_id == o.id)) or 0) + 1
    session.add(PaperOrderEvent(order_id=o.id, seq=seq, event=event, from_status=o.status,
                                to_status=to, bar_date=bar, details=details or {}))
    if to is not None:
        o.status = to
        o.version += 1


def policy(session: Session, pf: Portfolio) -> RiskPolicy | None:
    return session.scalar(select(RiskPolicy).where(RiskPolicy.portfolio_id == pf.id))


def place(session: Session, p: Principal, lake: LakeClient, pid: uuid.UUID,
          body: OrderIn) -> PaperOrder:
    pf = _paper(session, p, pid)
    old = session.scalar(select(PaperOrder).where(
        PaperOrder.portfolio_id == pf.id, PaperOrder.client_order_id == body.client_order_id))
    if old is not None:  # idempotent retry; a different order under the same id is refused
        if (old.symbol, old.side, old.quantity, old.order_type) != (
                body.symbol.upper(), body.side, body.quantity, body.order_type):
            raise Conflict("client_order_id already used for a different order")
        return old
    inst = resolve_listing(lake, body.exchange, body.symbol)
    latest = latest_market_date(lake, date.today())
    placed = min(body.placed_on, latest) if body.placed_on else latest
    _, rows = lake.prices_daily(instrument_id=inst["instrument_id"], exchange=inst["exchange_mic"],
                                start=placed - timedelta(days=10), end=placed, adjust="none",
                                as_of=placed)
    if not rows:
        raise DomainError(f"No price for {inst['symbol']} on or before {placed}")
    ref = float(body.limit_price or body.stop_price or rows[-1]["close"])
    o = PaperOrder(workspace_id=p.workspace_id, portfolio_id=pf.id, created_by=p.user_id,
                   client_order_id=body.client_order_id, instrument_id=inst["instrument_id"],
                   symbol=inst["symbol"], exchange=inst["exchange_mic"], side=body.side,
                   order_type=body.order_type, quantity=body.quantity,
                   limit_price=body.limit_price, stop_price=body.stop_price, tif=body.tif,
                   status="NEW", placed_on=placed, simulated_through=placed)
    session.add(o)
    session.flush()
    _event(session, o, "CREATED", None, placed, {"reference_price": ref})
    reason = _pre_trade(session, p, lake, pf, o, ref, placed)
    if reason:
        o.reason = reason
        _event(session, o, "REJECTED", "REJECTED", placed, {"reason": reason})
    else:
        _event(session, o, "ACCEPTED", "WORKING", placed)
    audit.record(session, "paper.order", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="paper_order", resource_id=o.id,
                 details={"status": o.status, "symbol": o.symbol, "side": o.side})
    return o


def _pre_trade(session: Session, p: Principal, lake: LakeClient, pf: Portfolio, o: PaperOrder,
               ref: float, on: date) -> str | None:
    book = psvc.book(session, lake, pf, on)
    pending = session.scalars(select(PaperOrder).where(
        PaperOrder.portfolio_id == pf.id, PaperOrder.status.in_(("WORKING", "PARTIALLY_FILLED")),
        PaperOrder.id != o.id))
    if o.side == "SELL":
        held = book.positions.get(o.instrument_id)
        reserved = sum(x.quantity - x.filled_quantity for x in pending
                       if x.side == "SELL" and x.instrument_id == o.instrument_id)
        have = (held.quantity if held else 0.0) - reserved
        if o.quantity > have + 1e-9:
            return f"sell {o.quantity:g} exceeds the {have:g} shares available (no shorting)"
        return None
    pol = policy(session, pf)
    if pol is not None and pol.state == "RISK_OFF":
        return f"portfolio is RISK_OFF ({pol.state_reason}); new buys are blocked"
    est = o.quantity * ref
    cost = COSTS.trade(est, True, None, None)["total"]
    committed = sum((x.quantity - x.filled_quantity) * (x.limit_price or x.stop_price or ref)
                    for x in pending if x.side == "BUY")
    if est + cost > book.cash - committed + 1e-6:
        return f"needs about {est + cost:,.0f} INR; {book.cash - committed:,.0f} available"
    if pol is not None and pol.max_position_weight:
        nav = psvc.holdings(session, p, lake, pf, on)["nav"]
        if nav > 0 and est / nav > pol.max_position_weight:
            return (f"order is {est / nav:.1%} of NAV, above the "
                    f"{pol.max_position_weight:.0%} position limit")
    return None


def cancel(session: Session, p: Principal, pid: uuid.UUID, oid: uuid.UUID) -> PaperOrder:
    pf = _paper(session, p, pid)
    o = session.get(PaperOrder, oid)
    if o is None or o.portfolio_id != pf.id:
        raise NotFound("Order not found")
    if o.status in pp.FINAL:
        raise Conflict(f"A {o.status} order cannot be cancelled")
    _event(session, o, "CANCELLED", "CANCELLED", None,
           {"unfilled": o.quantity - o.filled_quantity})
    return o


def simulate(session: Session, p: Principal, lake: LakeClient, pid: uuid.UUID) -> dict[str, int]:
    """Advance every working order through the bars after its last simulated day."""
    pf = _paper(session, p, pid)
    latest = latest_market_date(lake, date.today())
    fills = 0
    for o in list(session.scalars(select(PaperOrder).where(
            PaperOrder.portfolio_id == pf.id,
            PaperOrder.status.in_(("WORKING", "PARTIALLY_FILLED"))).order_by(
            PaperOrder.created_at))):
        start = o.simulated_through or o.placed_on
        if start >= latest:
            continue
        _, rows = lake.prices_daily(instrument_id=o.instrument_id, exchange=o.exchange,
                                    start=start + timedelta(days=1), end=latest, adjust="none",
                                    as_of=latest)
        sim = pp.SimOrder(o.side, o.quantity, o.filled_quantity, o.order_type, o.limit_price,
                          o.stop_price, o.tif, o.triggered, o.sessions)
        for r in rows:
            d = date.fromisoformat(str(r["trade_date"])[:10])
            if d <= start or not r.get("close"):
                continue
            bar = pp.DayBar(d, float(r.get("open") or r["close"]),
                            float(r.get("high") or r["close"]), float(r.get("low") or r["close"]),
                            float(r["close"]), float(r.get("volume") or 0))
            res = pp.step(sim, bar)
            o.triggered, o.sessions, o.simulated_through = sim.triggered, sim.sessions, d
            if res.triggered_now:
                _event(session, o, "STOP_TRIGGERED", None, d, {"stop": o.stop_price})
            if res.fill:
                f = res.fill
                fee = COSTS.trade(f.quantity * f.price, o.side == "BUY", None, None)["total"]
                psvc.add_transactions(session, _owner(o), lake, pf, [psvc.TxIn(
                    trade_date=d, type=o.side, symbol=o.symbol, exchange=o.exchange,
                    quantity=f.quantity, price=f.price, fees=round(fee, 4),
                    external_ref=f"paper:{o.id}:{d}")], source="paper")
                total = o.filled_quantity + f.quantity
                o.average_price = ((o.average_price or 0) * o.filled_quantity
                                   + f.price * f.quantity) / total
                o.filled_quantity, o.fees = total, o.fees + fee
                to = "FILLED" if sim.remaining <= 1e-9 else "PARTIALLY_FILLED"
                _event(session, o, "FILL", to, d, {"quantity": f.quantity, "price": f.price,
                                                   "fees": fee, "note": f.note})
                fills += 1
            if o.status == "FILLED":
                break
            if res.expired:
                _event(session, o, "EXPIRED", "EXPIRED", d,
                       {"unfilled": o.quantity - o.filled_quantity})
                break
    return {"fills": fills}


def _owner(o: PaperOrder) -> Principal:
    from quantera.identity.models import Role

    return Principal(o.created_by, o.workspace_id, uuid.uuid4(), Role.OWNER)


def reconcile(session: Session, p: Principal, lake: LakeClient, pid: uuid.UUID) -> dict[str, Any]:
    """Fills recorded on orders must equal the paper fills in the ledger, quantity by quantity."""
    pf = _paper(session, p, pid)
    orders = list(session.scalars(select(PaperOrder).where(PaperOrder.portfolio_id == pf.id)))
    ledger = list(session.scalars(select(PortfolioTransaction).where(
        PortfolioTransaction.portfolio_id == pf.id, PortfolioTransaction.source == "paper",
        PortfolioTransaction.voided_at.is_(None))))
    by_order: dict[str, float] = {}
    for t in ledger:
        oid = (t.external_ref or "").split(":")[1] if t.external_ref else ""
        by_order[oid] = by_order.get(oid, 0.0) + t.quantity
    breaks = [{"order_id": str(o.id), "order_filled": o.filled_quantity,
               "ledger_quantity": by_order.get(str(o.id), 0.0)}
              for o in orders if abs(o.filled_quantity - by_order.get(str(o.id), 0.0)) > 1e-9]
    return {"status": "RECONCILED" if not breaks else "BREAKS", "orders": len(orders),
            "ledger_fills": len(ledger), "breaks": breaks}


def set_policy(session: Session, p: Principal, pid: uuid.UUID, body: PolicyIn) -> RiskPolicy:
    pf = _paper(session, p, pid)
    pol = policy(session, pf) or RiskPolicy(workspace_id=p.workspace_id, portfolio_id=pf.id,
                                            state="NORMAL")
    for k, v in body.model_dump().items():
        setattr(pol, k, v)
    pol.updated_at = datetime.now(UTC)
    session.add(pol)
    session.flush()
    audit.record(session, "risk.policy", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="risk_policy", resource_id=pol.id, details=body.model_dump())
    return pol


def check_policy(session: Session, p: Principal, lake: LakeClient, pid: uuid.UUID
                 ) -> dict[str, Any]:
    """Run once per trading day (CLI job): the daily-loss rule compares NAV with the NAV of
    the previous check; the drawdown rule with the highest NAV seen."""
    pf = _paper(session, p, pid)
    pol = policy(session, pf)
    if pol is None:
        raise NotFound("No risk policy for this portfolio")
    h = psvc.holdings(session, p, lake, pf)
    nav, as_of = h["nav"], date.fromisoformat(h["as_of"])
    prev = pol.last_nav
    pol.nav_peak = max(pol.nav_peak or nav, nav)
    breaches = []

    def breach(kind: str, value: float, limit: float, action: str) -> None:
        breaches.append({"kind": kind, "value": value, "limit": limit, "action": action})
        if session.scalar(select(RiskBreach.id).where(RiskBreach.policy_id == pol.id,
                                                      RiskBreach.kind == kind,
                                                      RiskBreach.as_of == as_of)) is None:
            session.add(RiskBreach(policy_id=pol.id, kind=kind, as_of=as_of, value=value,
                                   limit=limit, action=action))

    if pol.daily_loss_limit and prev and nav / prev - 1 <= -pol.daily_loss_limit:
        breach("DAILY_LOSS", nav / prev - 1, -pol.daily_loss_limit, "RISK_OFF")
    dd = nav / pol.nav_peak - 1 if pol.nav_peak else 0.0
    if pol.max_drawdown and dd <= -pol.max_drawdown:
        breach("DRAWDOWN", dd, -pol.max_drawdown, "RISK_OFF")
    if pol.max_position_weight:
        for r in h["positions"]:
            if (r["weight"] or 0) > pol.max_position_weight:
                breach("CONCENTRATION", r["weight"], pol.max_position_weight, "REVIEW")
    if any(b["action"] == "RISK_OFF" for b in breaches) and pol.state != "RISK_OFF":
        pol.state = "RISK_OFF"
        pol.state_reason = "; ".join(f"{b['kind']} {b['value']:.1%}" for b in breaches
                                     if b["action"] == "RISK_OFF")
        audit.record(session, "risk.risk_off", actor=p.user_id, workspace=p.workspace_id,
                     resource_type="risk_policy", resource_id=pol.id,
                     details={"breaches": breaches})
    pol.last_nav, pol.updated_at = nav, datetime.now(UTC)
    return {"state": pol.state, "state_reason": pol.state_reason, "nav": nav,
            "drawdown": dd, "breaches": breaches}


def reset_policy(session: Session, p: Principal, pid: uuid.UUID, note: str) -> RiskPolicy:
    pf = _paper(session, p, pid)
    pol = policy(session, pf)
    if pol is None or pol.state != "RISK_OFF":
        raise Conflict("The portfolio is not RISK_OFF")
    pol.state, pol.state_reason, pol.updated_at = "NORMAL", None, datetime.now(UTC)
    audit.record(session, "risk.reset", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="risk_policy", resource_id=pol.id, details={"note": note})
    return pol


def order_out(session: Session, o: PaperOrder, events: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(o.id), "client_order_id": o.client_order_id, "symbol": o.symbol,
        "exchange": o.exchange, "side": o.side, "order_type": o.order_type,
        "quantity": o.quantity, "limit_price": o.limit_price, "stop_price": o.stop_price,
        "tif": o.tif, "status": o.status, "filled_quantity": o.filled_quantity,
        "average_price": o.average_price, "fees": o.fees, "placed_on": o.placed_on.isoformat(),
        "simulated_through": o.simulated_through.isoformat() if o.simulated_through else None,
        "reason": o.reason, "venue": "PAPER (simulated)"}
    if events:
        out["events"] = [{"seq": e.seq, "event": e.event, "from": e.from_status,
                          "to": e.to_status, "bar_date": e.bar_date.isoformat()
                          if e.bar_date else None, "details": e.details}
                         for e in session.scalars(select(PaperOrderEvent).where(
                             PaperOrderEvent.order_id == o.id).order_by(PaperOrderEvent.seq))]
    return out

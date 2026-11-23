"""Portfolios: ledger writes (validated by replaying the whole book), CSV holdings import,
valuation from lake prices, risk analysis and holding review states."""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from quant import features as fx
from quant import portfolio as pq
from quantera.analysis.models import DecisionRecord
from quantera.analysis.scan import BENCHMARK, latest_market_date
from quantera.core import audit
from quantera.core.errors import Conflict, DomainError, NotFound
from quantera.identity.service import Principal
from quantera.lake.client import LakeClient
from quantera.portfolios.models import Portfolio, PortfolioTransaction
from quantera.universes.service import EXCHANGES, FORMULA_PREFIXES, MAX_CSV_BYTES, _clean_symbol

TxType = Literal["BUY", "SELL", "DEPOSIT", "WITHDRAWAL", "DIVIDEND", "FEE"]
RISK_LOOKBACK_DAYS = 400


class PortfolioIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    is_paper: bool = False
    max_position_weight: float = Field(default=0.10, gt=0, le=1)


class TxIn(BaseModel):
    trade_date: date
    type: TxType
    symbol: str | None = Field(default=None, max_length=40)
    exchange: str = "NSE"
    quantity: float = Field(default=0, ge=0)
    price: float = Field(default=0, ge=0)
    fees: float = Field(default=0, ge=0)
    amount: float = Field(default=0, ge=0)
    notes: str | None = Field(default=None, max_length=300)
    external_ref: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _shape(self) -> TxIn:
        if self.type in ("BUY", "SELL"):
            if not self.symbol or self.quantity <= 0 or self.price <= 0:
                raise ValueError(f"{self.type} needs symbol, quantity > 0 and price > 0")
        elif self.type == "DIVIDEND":
            if not self.symbol or self.amount <= 0:
                raise ValueError("DIVIDEND needs symbol and amount > 0")
        elif self.amount <= 0:
            raise ValueError(f"{self.type} needs amount > 0")
        if self.trade_date > date.today():
            raise ValueError("trade_date is in the future")
        return self


def get(session: Session, p: Principal, pid: uuid.UUID) -> Portfolio:
    pf = session.get(Portfolio, pid)
    if pf is None or pf.workspace_id != p.workspace_id or pf.archived:
        raise NotFound("Portfolio not found")
    return pf


def create(session: Session, p: Principal, body: PortfolioIn) -> Portfolio:
    pf = Portfolio(workspace_id=p.workspace_id, created_by=p.user_id, name=body.name.strip(),
                   is_paper=body.is_paper, max_position_weight=body.max_position_weight)
    session.add(pf)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict(f"A portfolio named {body.name!r} already exists") from exc
    audit.record(session, "portfolio.create", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="portfolio", resource_id=pf.id)
    return pf


def active_txs(session: Session, pf: Portfolio) -> list[PortfolioTransaction]:
    return list(session.scalars(select(PortfolioTransaction).where(
        PortfolioTransaction.portfolio_id == pf.id, PortfolioTransaction.voided_at.is_(None))
        .order_by(PortfolioTransaction.trade_date, PortfolioTransaction.seq)))


def _as_dict(t: PortfolioTransaction) -> dict[str, Any]:
    return {"trade_date": t.trade_date, "type": t.type, "instrument_id": t.instrument_id,
            "symbol": t.symbol, "exchange": t.exchange, "quantity": t.quantity, "price": t.price,
            "fees": t.fees, "amount": t.amount}


class ShareFactors:
    """Cumulative split/bonus share multipliers from the lake's corporate actions."""

    def __init__(self, lake: LakeClient, exchanges: dict[str, str]) -> None:
        self.lake, self.exchanges = lake, exchanges
        self.cache: dict[str, list[tuple[date, float]]] = {}

    def __call__(self, iid: str, after: date, upto: date) -> float:
        if iid not in self.cache:
            acts = self.lake.corporate_actions(iid, self.exchanges.get(iid, "XNSE"))
            self.cache[iid] = [(date.fromisoformat(str(a["ex_date"])[:10]),
                                float(a.get("volume_factor") or 1.0)) for a in acts
                               if a.get("event") in ("SPLIT", "BONUS") and a.get("volume_factor")]
        f = 1.0
        for ex, v in self.cache[iid]:
            if after < ex <= upto:
                f *= v
        return f


def book(session: Session, lake: LakeClient | None, pf: Portfolio, as_of: date | None = None,
         extra: list[dict[str, Any]] | None = None) -> pq.Book:
    rows = [_as_dict(t) for t in active_txs(session, pf)] + (extra or [])
    rows.sort(key=lambda r: r["trade_date"])
    factors = (ShareFactors(lake, {r["instrument_id"]: r["exchange"] for r in rows
                                   if r.get("instrument_id")}) if lake else None)
    try:
        return pq.replay(rows, factors, as_of or date.today())
    except pq.LedgerError as exc:
        raise DomainError(str(exc)) from exc


def add_transactions(session: Session, p: Principal, lake: LakeClient, pf: Portfolio,
                     items: list[TxIn], source: str = "manual",
                     request_id: str | None = None) -> list[PortfolioTransaction]:
    """All-or-nothing: the whole book is replayed with the new rows before anything is saved."""
    resolved: list[dict[str, Any]] = []
    for it in items:
        row: dict[str, Any] = it.model_dump()
        if it.symbol:
            mic = EXCHANGES.get(it.exchange.upper())
            if mic is None:
                raise DomainError("exchange must be NSE or BSE")
            sym, why = _clean_symbol(it.symbol)
            if sym is None:
                raise DomainError(why or "empty symbol")
            hits = [h for h in lake.resolve(sym, mic) if h.get("exchange_mic") == mic]
            if len(hits) != 1:
                raise NotFound(f"{sym} is not a single listing on {mic}")
            row |= {"instrument_id": hits[0]["instrument_id"], "symbol": hits[0]["symbol"],
                    "exchange": mic}
        else:
            row |= {"instrument_id": None, "symbol": None, "exchange": None}
        resolved.append(row)
    book(session, lake, pf, extra=resolved)  # raises on oversell etc.
    seq = session.scalar(select(func.max(PortfolioTransaction.seq)).where(
        PortfolioTransaction.portfolio_id == pf.id)) or 0
    out = []
    for r in resolved:
        if r.get("external_ref") and session.scalar(select(PortfolioTransaction.id).where(
                PortfolioTransaction.portfolio_id == pf.id,
                PortfolioTransaction.external_ref == r["external_ref"])):
            continue  # idempotent re-send
        seq += 1
        t = PortfolioTransaction(portfolio_id=pf.id, workspace_id=pf.workspace_id, seq=seq,
                                 trade_date=r["trade_date"], type=r["type"],
                                 instrument_id=r["instrument_id"], symbol=r["symbol"],
                                 exchange=r["exchange"], quantity=r["quantity"],
                                 price=r["price"], fees=r["fees"], amount=r["amount"],
                                 notes=r.get("notes"), source=source,
                                 external_ref=r.get("external_ref"), created_by=p.user_id)
        session.add(t)
        out.append(t)
    session.flush()
    audit.record(session, "portfolio.transactions", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="portfolio", resource_id=pf.id, request_id=request_id,
                 details={"count": len(out), "source": source})
    return out


def void(session: Session, p: Principal, lake: LakeClient, pf: Portfolio, tx_id: uuid.UUID,
         reason: str) -> PortfolioTransaction:
    t = session.get(PortfolioTransaction, tx_id)
    if t is None or t.portfolio_id != pf.id:
        raise NotFound("Transaction not found")
    if t.voided_at is not None:
        raise Conflict("Already voided")
    t.voided_at, t.voided_by, t.void_reason = datetime.now(UTC), p.user_id, reason[:300]
    session.flush()
    book(session, lake, pf)  # voiding a buy that later sells depend on is refused
    audit.record(session, "portfolio.void", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="portfolio_transaction", resource_id=tx_id,
                 details={"reason": reason[:300]})
    return t


def import_holdings_csv(session: Session, p: Principal, lake: LakeClient, pf: Portfolio,
                        data: bytes, trade_date: date) -> list[PortfolioTransaction]:
    """Template: symbol,quantity,average_cost[,exchange]. Each row becomes a BUY at the
    average cost on ``trade_date``. Formula-looking cells are refused."""
    if len(data) > MAX_CSV_BYTES:
        raise DomainError("CSV too large")
    try:
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    except UnicodeDecodeError as exc:
        raise DomainError("CSV must be UTF-8") from exc
    need = {"symbol", "quantity", "average_cost"}
    if not rows or not need <= {k.strip().lower() for k in rows[0]}:
        raise DomainError("CSV needs columns symbol, quantity, average_cost")
    items, errors = [], []
    for i, raw in enumerate(rows, start=2):
        r = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        if any(v.startswith(FORMULA_PREFIXES) for v in r.values()):
            errors.append(f"row {i}: looks like a spreadsheet formula")
            continue
        try:
            items.append(TxIn(trade_date=trade_date, type="BUY", symbol=r["symbol"],
                              exchange=r.get("exchange") or "NSE",
                              quantity=float(r["quantity"]), price=float(r["average_cost"]),
                              external_ref=f"csv:{trade_date}:{r['symbol'].upper()}"))
        except (ValueError, KeyError) as exc:
            errors.append(f"row {i}: {str(exc)[:120]}")
    if errors:
        raise DomainError("CSV rejected; nothing was imported", {"errors": errors[:50]})
    return add_transactions(session, p, lake, pf, items, source="csv")


# --------------------------------------------------------------------------- views
def _last_close(lake: LakeClient, iid: str, mic: str, as_of: date) -> tuple[float, str] | None:
    _, rows = lake.prices_daily(instrument_id=iid, exchange=mic, start=as_of - timedelta(days=15),
                                end=as_of, adjust="none", as_of=as_of)
    rows = [r for r in rows if r.get("close")]
    return (float(rows[-1]["close"]), str(rows[-1]["trade_date"])[:10]) if rows else None


def holdings(session: Session, p: Principal, lake: LakeClient, pf: Portfolio,
             as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or latest_market_date(lake, date.today())
    b = book(session, lake, pf, as_of)
    rows: list[dict[str, Any]] = []
    invested, unpriced = 0.0, []
    for pos in b.positions.values():
        if pos.quantity <= 1e-9:
            continue
        px = _last_close(lake, pos.instrument_id, pos.exchange, as_of)
        mv = pos.quantity * px[0] if px else None
        if mv is None:
            unpriced.append(pos.symbol)
        else:
            invested += mv
        rows.append({"instrument_id": pos.instrument_id, "symbol": pos.symbol,
                     "exchange": pos.exchange, "route": f"/stocks/{pos.exchange}/{pos.symbol}",
                     "quantity": pos.quantity, "average_cost": pos.average_cost, "cost": pos.cost,
                     "price": px[0] if px else None, "price_date": px[1] if px else None,
                     "market_value": mv, "unrealized_pnl": (mv - pos.cost) if mv else None,
                     "realized_pnl": pos.realized_pnl, "dividends": pos.dividends})
    nav = invested + b.cash
    for r in rows:
        r["weight"] = r["market_value"] / nav if r["market_value"] and nav > 0 else None
    rows.sort(key=lambda r: -(r["market_value"] or 0))
    return {"portfolio": {"id": str(pf.id), "name": pf.name, "is_paper": pf.is_paper,
                          "base_currency": pf.base_currency},
            "as_of": as_of.isoformat(), "nav": nav, "invested": invested, "cash": b.cash,
            "fees": b.fees, "external_flows": b.external_flows,
            "realized_pnl": sum(pos.realized_pnl for pos in b.positions.values()),
            "positions": rows, "unpriced": unpriced,
            "warnings": [f"no price on or before {as_of}: {', '.join(unpriced)}"] if unpriced
            else [], "pricing": "last traded close (unadjusted); quantities adjusted for "
                                "splits and bonuses since each trade"}


_SECTORS: dict[str, dict[str, str]] = {}


def sector_map(lake: LakeClient) -> dict[str, str]:
    key = str(lake.health().get("release"))
    if key not in _SECTORS:
        m: dict[str, str] = {}
        for ind in lake.industries():
            for r in lake.industry_members(ind["industry"]).rows:
                m[r["instrument_id"]] = ind["industry"]
        _SECTORS[key] = m
    return _SECTORS[key]


def risk(session: Session, p: Principal, lake: LakeClient, pf: Portfolio,
         as_of: date | None = None) -> dict[str, Any]:
    h = holdings(session, p, lake, pf, as_of)
    as_of_d = date.fromisoformat(h["as_of"])
    pos = [r for r in h["positions"] if r["market_value"]]
    out: dict[str, Any] = {"as_of": h["as_of"], "nav": h["nav"], "cash": h["cash"],
                           "cash_weight": h["cash"] / h["nav"] if h["nav"] > 0 else None}
    if not pos:
        return out | {"status": "INSUFFICIENT DATA", "reason": "no priced positions"}
    start = as_of_d - timedelta(days=RISK_LOOKBACK_DAYS)
    series: list[dict[date, float]] = []
    adv: list[float | None] = []
    for r in pos:
        _, rows = lake.prices_daily(instrument_id=r["instrument_id"], exchange=r["exchange"],
                                    start=start, end=as_of_d, adjust="total", as_of=as_of_d)
        b = fx.Bars.from_rows(rows)
        series.append(dict(zip(b.dates, b.close, strict=True)))
        adv.append(fx.price_features(b).get("ADV_VALUE_20D") if len(b) else None)
    bench_rows = lake.index_daily(BENCHMARK, start, as_of_d)
    bench = {date.fromisoformat(str(r["trade_date"])[:10]): float(r["close"]) for r in bench_rows}
    common = sorted(set(bench).intersection(*[set(s) for s in series]))[-253:]
    prices = np.array([[s[d] for s in series] for d in common])
    rets = prices[1:] / prices[:-1] - 1 if len(common) > 1 else np.empty((0, len(pos)))
    bp = np.array([bench[d] for d in common])
    brets = bp[1:] / bp[:-1] - 1 if len(common) > 1 else None
    mv = np.array([r["market_value"] for r in pos])
    w = mv / mv.sum()
    sectors = sector_map(lake)
    rep = pq.risk_report(w, rets, brets, [sectors.get(r["instrument_id"]) for r in pos], mv, adv,
                         [r["symbol"] for r in pos])
    return out | rep | {"weights_basis": "share of invested value (cash excluded)",
                        "benchmark": BENCHMARK, "window_days": len(rets)}


def review(session: Session, p: Principal, lake: LakeClient, pf: Portfolio) -> dict[str, Any]:
    """Holding states from the latest decision record of each holding in this workspace."""
    h = holdings(session, p, lake, pf)
    rows = []
    for r in h["positions"]:
        d = session.scalar(select(DecisionRecord).where(
            DecisionRecord.workspace_id == p.workspace_id,
            DecisionRecord.instrument_id == r["instrument_id"])
            .order_by(DecisionRecord.as_of.desc(), DecisionRecord.created_at.desc()).limit(1))
        state, why = pq.holding_state(d.state if d else None,
                                      (d.pillars or {}).get("RISK") if d else None,
                                      r["weight"] or 0.0, pf.max_position_weight)
        rows.append({"symbol": r["symbol"], "route": r["route"], "weight": r["weight"],
                     "holding_state": state, "reason": why,
                     "decision": {"state": d.state, "as_of": d.as_of.isoformat(),
                                  "job_id": str(d.job_id), "opportunity": d.opportunity}
                     if d else None})
    return {"as_of": h["as_of"], "rows": rows,
            "notes": ["Review states ask for a human decision; they are not orders.",
                      "Run a scan that includes the holdings to refresh decisions "
                      "(POST /api/v1/portfolios/{id}/review/scan)."]}


Sessions = Callable[[], Session]

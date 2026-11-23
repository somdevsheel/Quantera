"""Portfolio accounting and risk (pure).

Accounting (average-cost method, INR):
  BUY  adds quantity; cost += qty * price + fees
  SELL reduces quantity; realised P&L = qty * price - fees - qty * average cost
  DEPOSIT / WITHDRAWAL / DIVIDEND / FEE move cash only
  Splits and bonuses after the trade date multiply the quantity by the share factor
  (1 / price factor) and leave the cost unchanged.
  A SELL beyond the held quantity is rejected (no short positions in a cash book).

Risk (current weights, historical daily total returns, 252 days a year):
  volatility          sqrt(w' S w) * sqrt(252), S = sample covariance of daily returns
  beta                regression of portfolio returns on the benchmark
  VaR / ES 95% 1d     historical simulation of today's weights; parametric VaR = 1.645 sigma
  risk contribution   RC_i = w_i (S w)_i / (w' S w); contributions sum to 1
  concentration       HHI = sum w_i^2; effective names = 1 / HHI; top-1 and top-5 weight
  stress              market -10% (beta-scaled); largest sector -20%; correlation spike
                      (all pairwise correlations -> 0.9, same volatilities); volatility x2
  liquidity           days to exit each position trading 10% of its 20-day median value
Weights exclude cash; exposure figures are shares of invested value unless stated.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

ANN = 252
Z95 = 1.6448536269514722


@dataclass
class Position:
    instrument_id: str
    symbol: str
    exchange: str
    quantity: float = 0.0
    cost: float = 0.0  # total cost of the open quantity (INR, incl. fees)
    realized_pnl: float = 0.0
    dividends: float = 0.0

    @property
    def average_cost(self) -> float | None:
        return self.cost / self.quantity if self.quantity > 0 else None


@dataclass
class Book:
    positions: dict[str, Position] = field(default_factory=dict)
    cash: float = 0.0
    fees: float = 0.0
    external_flows: float = 0.0


class LedgerError(ValueError):
    pass


def replay(txs: Sequence[dict[str, Any]],
           share_factor: Callable[[str, date, date], float] | None = None,
           as_of: date | None = None) -> Book:
    """``txs`` sorted by trade_date then sequence. ``share_factor(iid, after, upto)`` returns
    the cumulative share multiplier of splits/bonuses with after < ex_date <= upto. They are
    applied in the timeline, before trades on their ex-date (which are in post-action shares),
    so a sell after a split is checked against the post-split quantity."""
    book = Book()
    last: date | None = None

    def roll(upto: date) -> None:
        if share_factor is None or last is None or upto <= last:
            return
        for pos in book.positions.values():
            if pos.quantity > 0:
                pos.quantity *= share_factor(pos.instrument_id, last, upto)

    for t in txs:
        roll(t["trade_date"])
        last = t["trade_date"] if last is None or t["trade_date"] > last else last
        kind, amt = t["type"], float(t.get("amount") or 0)
        if kind in ("DEPOSIT", "WITHDRAWAL"):
            sign = 1 if kind == "DEPOSIT" else -1
            book.cash += sign * amt
            book.external_flows += sign * amt
            continue
        if kind == "FEE":
            book.cash -= amt
            book.fees += amt
            continue
        iid = t["instrument_id"]
        pos = book.positions.setdefault(iid, Position(iid, t["symbol"], t["exchange"]))
        if kind == "DIVIDEND":
            book.cash += amt
            pos.dividends += amt
            continue
        qty, px, fee = float(t["quantity"]), float(t["price"]), float(t.get("fees") or 0)
        if qty <= 0 or px <= 0:
            raise LedgerError(f"{kind} needs positive quantity and price")
        book.fees += fee
        if kind == "BUY":
            pos.quantity += qty
            pos.cost += qty * px + fee
            book.cash -= qty * px + fee
        elif kind == "SELL":
            if qty > pos.quantity + 1e-9:
                raise LedgerError(f"SELL {qty:g} {pos.symbol} on {t['trade_date']} exceeds the "
                                  f"{pos.quantity:g} held")
            avg = pos.cost / pos.quantity
            pos.realized_pnl += qty * px - fee - qty * avg
            pos.cost -= qty * avg
            pos.quantity -= qty
            book.cash += qty * px - fee
        else:
            raise LedgerError(f"unknown transaction type {kind}")
    if as_of is not None:
        roll(as_of)
    return book


def risk_report(weights: np.ndarray, returns: np.ndarray, bench: np.ndarray | None,
                sectors: Sequence[str | None], values: np.ndarray, adv: Sequence[float | None],
                names: Sequence[str]) -> dict[str, Any]:
    """``returns``: T x N daily returns aligned to ``weights`` (N); ``bench``: T."""
    w = np.asarray(weights, dtype=float)
    out: dict[str, Any] = {}
    hhi = float((w ** 2).sum())
    order = np.argsort(-w)
    out["concentration"] = {"hhi": hhi, "effective_names": 1 / hhi if hhi > 0 else None,
                            "top1": float(w[order[:1]].sum()), "top5": float(w[order[:5]].sum()),
                            "largest": names[order[0]] if len(w) else None}
    sec: dict[str, float] = {}
    for s, x in zip(sectors, w, strict=True):
        sec[s or "Unclassified"] = sec.get(s or "Unclassified", 0.0) + float(x)
    out["sector_exposure"] = dict(sorted(sec.items(), key=lambda kv: -kv[1]))
    days = [float(v) / (0.10 * a) if a and a > 0 else None
            for v, a in zip(values, adv, strict=True)]
    out["liquidity"] = {"days_to_exit_at_10pct_adv": dict(zip(names, days, strict=True)),
                        "max_days": max((d for d in days if d is not None), default=None)}
    r = np.asarray(returns, dtype=float)
    if r.shape[0] < 60 or r.shape[1] != len(w):
        out["status"] = "INSUFFICIENT DATA"
        return out
    cov = np.cov(r, rowvar=False, ddof=1)
    cov = np.atleast_2d(cov)
    var_d = float(w @ cov @ w)
    sd_d = math.sqrt(max(var_d, 0.0))
    port = r @ w
    q = float(np.quantile(port, 0.05))
    out |= {"status": "OK", "observations": int(r.shape[0]),
            "volatility": sd_d * math.sqrt(ANN),
            "var_95_1d_hist": -q, "es_95_1d_hist": float(-port[port <= q].mean()),
            "var_95_1d_param": Z95 * sd_d,
            "max_drawdown_hypothetical": float(
                (np.cumprod(1 + port) / np.maximum.accumulate(np.cumprod(1 + port)) - 1).min())}
    rc = w * (cov @ w) / var_d if var_d > 0 else np.zeros_like(w)
    out["risk_contribution"] = dict(zip(names, (float(x) for x in rc), strict=True))
    sds = np.sqrt(np.diag(cov))
    corr = cov / np.outer(sds, sds) if (sds > 0).all() else None
    if corr is not None and len(w) > 1:
        iu = np.triu_indices(len(w), 1)
        out["average_pairwise_correlation"] = float(corr[iu].mean())
    beta = None
    if bench is not None and len(bench) == r.shape[0]:
        b = np.asarray(bench, dtype=float)
        vb = b.var(ddof=1)
        beta = float(np.cov(port, b, ddof=1)[0, 1] / vb) if vb > 0 else None
    out["beta"] = beta
    stress = []
    if beta is not None:
        stress.append({"scenario": "market -10%", "pnl_pct": -0.10 * beta,
                       "note": "beta-scaled; ignores stock-specific moves"})
    if sec:
        big, wt = next(iter(out["sector_exposure"].items()))
        stress.append({"scenario": f"{big} sector -20%", "pnl_pct": -0.20 * wt,
                       "note": "only the largest sector falls"})
    if len(w) > 1:
        spike = np.full_like(cov, 0.9)
        np.fill_diagonal(spike, 1.0)
        cs = np.outer(sds, sds) * spike
        stress.append({"scenario": "correlation spike (rho = 0.9)",
                       "var_95_1d": Z95 * math.sqrt(max(float(w @ cs @ w), 0.0)),
                       "note": "diversification disappears; same volatilities"})
    stress.append({"scenario": "volatility x2", "var_95_1d": 2 * Z95 * sd_d,
                   "note": "parametric VaR with doubled volatility"})
    out["stress"] = stress
    return out


HOLDING_STATES = ("HOLD", "ADD REVIEW", "TRIM REVIEW", "SELL REVIEW", "RISK ALERT",
                  "INSUFFICIENT DATA")


def holding_state(decision_state: str | None, risk_pillar: float | None, weight: float,
                  max_weight: float = 0.10, alert_risk: float = 5.0) -> tuple[str, str]:
    """(state, reason). Review states ask for a human decision; they are not orders."""
    if decision_state is None or decision_state in ("INSUFFICIENT DATA", "STALE DATA"):
        return "INSUFFICIENT DATA", "no current decision record for this holding"
    if risk_pillar is not None and risk_pillar < alert_risk:
        return "RISK ALERT", f"risk percentile {risk_pillar:.0f} is extreme"
    if decision_state == "AVOID":
        return "SELL REVIEW", "the stock is blocked by risk in the latest scan"
    if weight > max_weight:
        return "TRIM REVIEW", f"weight {weight:.1%} exceeds the {max_weight:.0%} limit"
    if decision_state == "NO TRADE":
        return "TRIM REVIEW", "weak evidence or illiquid in the latest scan"
    if decision_state == "BUY CANDIDATE":
        return "ADD REVIEW", "strong evidence and room under the weight limit"
    return "HOLD", "no rule asks for a change"

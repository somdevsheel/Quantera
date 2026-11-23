"""Daily backtest engine, Indian transaction costs, walk-forward selection, multiple-testing
control. Pure and deterministic.

Timeline (no look-ahead): on each rebalance date the signal uses only data up to that close;
trades execute at the close ``lag`` sessions later (default 1). Between rebalances holdings
drift with prices. Prices are total-return closes (splits, bonuses, dividends).

Universe: whatever ``universe_at(date)`` returns for that date. Use a point-in-time universe
(e.g. top-N liquidity on that date, delisted stocks included) to avoid survivorship bias.

Delisting / missing prices: a held stock with no price for ``stale_days`` sessions is sold at
its last price, and the event is recorded.

Costs per trade of value V (all configurable; defaults approximate NSE delivery trades):
  brokerage  b bps of V                      GST 18% on (brokerage + exchange + SEBI fees)
  STT        10 bps of V (buy and sell)      exchange transaction 0.297 bps, SEBI 0.01 bps
  stamp duty 1.5 bps of V (buy only)         spread: half the quoted spread
  impact     k * sigma_daily * sqrt(V / ADV) * V  (square-root law; sigma, ADV from 20 days)
  borrow     annual rate on short value, charged daily (shorts are simulation only)
Metrics: CAGR, volatility, Sharpe, Sortino, max drawdown, Calmar, turnover, hit rate (share of
positive months), profit factor (daily gains / losses), worst month, VaR/ES, costs; versus a
benchmark. Walk-forward: parameters chosen on each training window by Sharpe, then used on the
next test window; the stitched test returns are the out-of-sample result.
Deflated Sharpe ratio (Bailey & Lopez de Prado, 2014): the probability that the true Sharpe
exceeds the best Sharpe expected from N trials of pure noise; uses skew and kurtosis.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from statistics import NormalDist
from typing import Any

import numpy as np

from quant import stats

N01 = NormalDist()
EULER = 0.5772156649015329
VERSION = "backtest_v1"


@dataclass(frozen=True)
class Costs:
    brokerage_bps: float = 3.0
    stt_bps: float = 10.0
    exchange_bps: float = 0.297
    sebi_bps: float = 0.01
    stamp_bps_buy: float = 1.5
    gst_rate: float = 0.18
    spread_bps: float = 10.0  # full quoted spread; half is paid per trade
    impact_coef: float = 0.1
    borrow_annual: float = 0.08

    def trade(self, value: float, buy: bool, adv: float | None,
              sigma: float | None) -> dict[str, float]:
        v = abs(value)
        bro = v * self.brokerage_bps / 1e4
        exch = v * self.exchange_bps / 1e4
        sebi = v * self.sebi_bps / 1e4
        out = {"brokerage": bro, "stt": v * self.stt_bps / 1e4, "exchange": exch, "sebi": sebi,
               "stamp": v * self.stamp_bps_buy / 1e4 if buy else 0.0,
               "gst": self.gst_rate * (bro + exch + sebi), "spread": v * self.spread_bps / 2e4,
               "impact": (self.impact_coef * sigma * math.sqrt(v / adv) * v
                          if adv and adv > 0 and sigma and sigma > 0 else 0.0)}
        out["total"] = sum(out.values())
        return out


@dataclass
class Config:
    rebalance: str = "M"  # M (month end) | Q
    lag: int = 1
    selection: str = "TOP_QUANTILE"  # TOP_QUANTILE | TOP_N
    quantile: float = 0.2
    top_n: int = 20
    weighting: str = "EQUAL"  # EQUAL | INVERSE_VOL
    max_weight: float = 0.10
    long_short: bool = False
    stale_days: int = 5
    capital: float = 10_000_000.0
    costs: Costs = field(default_factory=Costs)


def rebalance_dates(days: Sequence[date], freq: str) -> list[date]:
    out = []
    for i, d in enumerate(days[:-1]):
        nxt = days[i + 1]
        if (freq == "M" and nxt.month != d.month) or \
                (freq == "Q" and nxt.month != d.month and d.month in (3, 6, 9, 12)):
            out.append(d)
    return out


def _weights(scores: dict[str, float], vol: dict[str, float | None], c: Config) -> dict[str, float]:
    ranked = sorted(scores, key=lambda k: -scores[k])
    k = (max(1, int(len(ranked) * c.quantile)) if c.selection == "TOP_QUANTILE"
         else min(c.top_n, len(ranked)))
    longs = ranked[:k]
    shorts = ranked[-k:] if c.long_short and len(ranked) >= 2 * k else []

    def leg(names: list[str]) -> dict[str, float]:
        if not names:
            return {}
        if c.weighting == "INVERSE_VOL":
            iv = {n: 1 / v for n in names if (v := vol.get(n)) and v > 0}
            tot = sum(iv.values())
            raw = {n: iv[n] / tot for n in iv} if tot else {}
        else:
            raw = {n: 1 / len(names) for n in names}
        return cap_weights(raw, c.max_weight)

    w = leg(longs)
    if shorts:
        w = {n: x * 0.5 for n, x in w.items()} | {n: -x * 0.5 for n, x in leg(shorts).items()}
    return w


def cap_weights(raw: dict[str, float], cap: float) -> dict[str, float]:
    """Normalise to 1, then cap each weight and hand the excess to the uncapped names in
    proportion. If every name is capped the rest stays in cash (weights sum below 1)."""
    tot = sum(raw.values())
    if tot <= 0:
        return {}
    w = {n: v / tot for n, v in raw.items()}
    fixed: dict[str, float] = {}
    while True:
        over = [n for n, v in w.items() if v > cap + 1e-12]
        if not over:
            return fixed | w
        for n in over:
            fixed[n] = cap
            del w[n]
        free = 1.0 - sum(fixed.values())
        rest = sum(w.values())
        if not w or rest <= 0:
            return fixed
        w = {n: v / rest * free for n, v in w.items()}


def run(days: Sequence[date], prices: dict[str, dict[date, float]],
        value: dict[str, dict[date, float]], universe_at: Callable[[date], list[str]],
        signal: Callable[[date, list[str]], dict[str, float]], c: Config,
        bench: dict[date, float] | None = None) -> dict[str, Any]:
    days = list(days)
    rebal = set(rebalance_dates(days, c.rebalance))
    exec_on: dict[date, date] = {}
    for i, d in enumerate(days):
        if d in rebal and i + c.lag < len(days):
            exec_on[days[i + c.lag]] = d
    cash = c.capital
    hold: dict[str, float] = {}  # name -> shares (negative = short)
    last_px: dict[str, tuple[date, float]] = {}
    nav, trades, events, turnover = [], [], [], []
    cost_tot: dict[str, float] = {}
    pending: dict[date, dict[str, float]] = {}
    for i, d in enumerate(days):
        for n, series in prices.items():
            if d in series:
                last_px[n] = (d, series[d])
        # delisting / stale prices: sell at the last price
        for n in list(hold):
            lp = last_px.get(n)
            if lp is None or sum(1 for x in days[max(0, i - c.stale_days):i + 1]
                                 if x > lp[0]) >= c.stale_days:
                px = lp[1] if lp else 0.0
                cash += hold[n] * px
                events.append({"date": d.isoformat(), "event": "DELISTED_OR_STALE", "name": n,
                               "price": px})
                del hold[n]
        if d in rebal:
            names = [n for n in universe_at(d) if n in last_px and last_px[n][0] == d]
            if names:
                scores = signal(d, names)
                vol = {n: _vol(prices[n], days, i) for n in scores}
                pending[d] = _weights(scores, vol, c)
        if d in exec_on and exec_on[d] in pending:
            target = pending.pop(exec_on[d])
            equity = cash + sum(q * last_px[n][1] for n, q in hold.items())
            traded = 0.0
            for n in set(target) | set(hold):
                if n not in last_px or last_px[n][0] != d:
                    continue  # no price today: cannot trade it
                px = last_px[n][1]
                cur = hold.get(n, 0.0) * px
                tgt = target.get(n, 0.0) * equity
                dv = tgt - cur
                if abs(dv) < 1e-6 * equity:
                    continue
                adv = _adv(value.get(n, {}), days, i)
                sig = _vol(prices[n], days, i, 20, annual=False)
                fee = c.costs.trade(dv, dv > 0, adv, sig)
                for k, v in fee.items():
                    cost_tot[k] = cost_tot.get(k, 0.0) + v
                hold[n] = hold.get(n, 0.0) + dv / px
                if abs(hold[n]) < 1e-9:
                    del hold[n]
                cash -= dv + fee["total"]
                traded += abs(dv)
                trades.append({"date": d.isoformat(), "name": n, "value": dv,
                               "price": px, "cost": fee["total"]})
            turnover.append(traded / equity / 2 if equity > 0 else 0.0)
        short_val = sum(-q * last_px[n][1] for n, q in hold.items() if q < 0)
        cash -= short_val * c.costs.borrow_annual / 252
        nav.append(cash + sum(q * last_px[n][1] for n, q in hold.items() if n in last_px))
    return _report(days, np.array(nav), bench, trades, events, turnover, cost_tot, c)


def _vol(series: dict[date, float], days: Sequence[date], i: int, n: int = 60,
         annual: bool = True) -> float | None:
    px = [series[d] for d in days[max(0, i - n):i + 1] if d in series]
    if len(px) < max(10, n // 2):
        return None
    r = np.diff(np.log(px))
    sd = float(r.std(ddof=1))
    return sd * math.sqrt(252) if annual else sd


def _adv(series: dict[date, float], days: Sequence[date], i: int) -> float | None:
    v = [series[d] for d in days[max(0, i - 20):i] if d in series]
    return float(np.median(v)) if v else None


def _report(days: Sequence[date], nav: np.ndarray, bench: dict[date, float] | None,
            trades: list[dict[str, Any]], events: list[dict[str, Any]], turnover: list[float],
            costs: dict[str, float], c: Config) -> dict[str, Any]:
    b = np.array([bench.get(d, np.nan) for d in days]) if bench else None
    if b is not None and np.isnan(b).any():
        b = None
    summ = stats.summary(nav, b, min_obs=20)
    r = nav[1:] / nav[:-1] - 1
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    monthly: dict[str, float] = {}
    start = nav[0]
    for k, d in enumerate(days):
        if k + 1 == len(days) or days[k + 1].month != d.month:
            monthly[f"{d.year}-{d.month:02d}"] = float(nav[k] / start - 1)
            start = nav[k]
    yearly: dict[str, float] = {}
    for month, v in monthly.items():
        y = month[:4]
        yearly[y] = (1 + yearly.get(y, 0.0)) * (1 + v) - 1
    mv = list(monthly.values())
    dd = nav / np.maximum.accumulate(nav) - 1
    return {"version": VERSION, "config": asdict(c),
            "metrics": summ | {"hit_rate_months": float(np.mean([x > 0 for x in mv])) if mv
                               else None,
                               "profit_factor": float(gains / losses) if losses > 0 else None,
                               "worst_month": min(mv) if mv else None,
                               "avg_turnover": float(np.mean(turnover)) if turnover else 0.0,
                               "rebalances": len(turnover),
                               "total_costs": float(sum(v for k, v in costs.items()
                                                        if k != "total")),
                               "cost_drag_pct_of_capital": float(
                                   costs.get("total", 0.0) / c.capital)},
            "costs": {k: float(v) for k, v in costs.items()},
            "monthly": monthly, "yearly": yearly,
            "equity": [{"date": d.isoformat(), "nav": float(v), "drawdown": float(x)}
                       for d, v, x in zip(days, nav, dd, strict=True)],
            "benchmark": [{"date": d.isoformat(), "level": float(x)} for d, x in
                          zip(days, b / b[0], strict=True)] if b is not None else None,
            "trades": trades[-2000:], "trade_count": len(trades), "events": events,
            "returns": [float(x) for x in r]}


# --------------------------------------------------------------------------- validation
def walk_forward(days: Sequence[date], returns_by_param: dict[str, Sequence[float]],
                 train: int = 504, test: int = 126) -> dict[str, Any]:
    """``returns_by_param``: daily returns of the same strategy under each parameter set over
    ``days[1:]``. For each fold, pick the best Sharpe on the training window, then take that
    parameter set's returns on the following test window."""
    names = list(returns_by_param)
    r = {k: np.asarray(v, dtype=float) for k, v in returns_by_param.items()}
    n = len(next(iter(r.values())))
    folds, oos = [], []
    start = train
    while start + 20 <= n:
        end = min(start + test, n)
        tr = {k: r[k][start - train:start] for k in names}
        sharpe = {k: (v.mean() / v.std(ddof=1) * math.sqrt(252)) if v.std(ddof=1) > 0 else -1e9
                  for k, v in tr.items()}
        best = max(sharpe, key=lambda k: sharpe[k])
        seg = r[best][start:end]
        oos.append(seg)
        folds.append({"train_end": days[start].isoformat(), "test_end": days[end].isoformat(),
                      "chosen": best, "train_sharpe": float(sharpe[best]),
                      "test_return": float(np.prod(1 + seg) - 1)})
        start = end
    joined = np.concatenate(oos) if oos else np.array([])
    out: dict[str, Any] = {"folds": folds, "train_days": train, "test_days": test,
                           "parameter_sets": names}
    if len(joined) > 20:
        nav = np.cumprod(np.r_[1.0, 1 + joined])
        out["out_of_sample"] = stats.summary(nav, min_obs=20)
    return out


def deflated_sharpe(returns: Sequence[float], trials: int,
                    trial_sharpes: Sequence[float] | None = None) -> dict[str, Any]:
    """Sharpe ratios are per period (daily) inside the formula; reported annualised too."""
    r = np.asarray(returns, dtype=float)
    t = len(r)
    if t < 30 or r.std(ddof=1) == 0:
        return {"status": "INSUFFICIENT DATA"}
    sr = r.mean() / r.std(ddof=1)
    z = (r - r.mean()) / r.std(ddof=0)
    skew, kurt = float((z ** 3).mean()), float((z ** 4).mean())
    if trials > 1:
        var = (float(np.var(trial_sharpes, ddof=1)) if trial_sharpes is not None
               and len(trial_sharpes) > 1 else 1.0 / t)
        sr0 = math.sqrt(var) * ((1 - EULER) * N01.inv_cdf(1 - 1 / trials)
                                + EULER * N01.inv_cdf(1 - 1 / (trials * math.e)))
    else:
        sr0 = 0.0
    den = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr ** 2, 1e-12))
    p = N01.cdf((sr - sr0) * math.sqrt(t - 1) / den)
    return {"sharpe_annual": float(sr * math.sqrt(252)),
            "benchmark_sharpe_annual": float(sr0 * math.sqrt(252)), "trials": trials,
            "deflated_sharpe_probability": float(p), "skew": skew, "kurtosis": kurt,
            "observations": t,
            "interpretation": "probability the true Sharpe exceeds the best expected from "
                              f"{trials} trial(s) of noise; below 0.95 the result may be luck"}

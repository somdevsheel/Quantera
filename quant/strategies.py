"""Strategy library: the 20 families with required data, implementation, validation,
limitations and an honest status; plus the signal functions of the implemented ones.

Statuses: PLANNED | DATA_BLOCKED (with blocked_by) | RESEARCH | BACKTEST_PASSED |
PAPER_TRADING | APPROVED | DISABLED. Nothing ships above RESEARCH: promotion is a governance
decision with recorded evidence. No production readiness is claimed for market making,
options, fixed income, digital assets or any data-blocked family.

Signals take (as_of, names, data) and return {name: score} using ONLY data on or before
as_of. A stock without the inputs a signal needs gets no score (it is not ranked), never a
neutral one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Protocol

import numpy as np

STATUSES = ("PLANNED", "DATA_BLOCKED", "RESEARCH", "BACKTEST_PASSED", "PAPER_TRADING",
            "APPROVED", "DISABLED")


class Data(Protocol):
    def closes(self, name: str, as_of: date, n: int) -> np.ndarray: ...
    def fundamentals(self, name: str, as_of: date) -> list[dict[str, Any]]: ...


Signal = Callable[[date, list[str], Data], dict[str, float]]


def _ret(c: np.ndarray, back: int, skip: int = 0) -> float | None:
    if len(c) <= back:
        return None
    return float(c[-1 - skip] / c[-1 - back] - 1)


def momentum_12_1(as_of: date, names: list[str], d: Data,
                  lookback: int = 252) -> dict[str, float]:
    out = {}
    for n in names:
        v = _ret(d.closes(n, as_of, lookback + 1), lookback, skip=21)
        if v is not None:
            out[n] = v
    return out


def trend_sma200(as_of: date, names: list[str], d: Data) -> dict[str, float]:
    out = {}
    for n in names:
        c = d.closes(n, as_of, 200)
        if len(c) >= 200:
            out[n] = float(c[-1] / c.mean() - 1)
    return out


def reversal_1m(as_of: date, names: list[str], d: Data) -> dict[str, float]:
    out = {}
    for n in names:
        v = _ret(d.closes(n, as_of, 22), 21)
        if v is not None:
            out[n] = -v  # last month's losers rank highest
    return out


def low_volatility(as_of: date, names: list[str], d: Data) -> dict[str, float]:
    out = {}
    for n in names:
        c = d.closes(n, as_of, 253)
        if len(c) >= 200:
            out[n] = -float(np.diff(np.log(c)).std(ddof=1))
    return out


def _ttm(rows: list[dict[str, Any]]) -> dict[str, float] | None:
    q: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("period_type") == "Quarterly" and r.get("to_date"):
            k = str(r["to_date"])[:10]
            if k not in q or r.get("consolidated") == "Consolidated":
                q[k] = r
    last = [q[k] for k in sorted(q)[-4:]]
    if len(last) < 4:
        return None
    eps = [x.get("eps_basic") for x in last]
    rev = [x.get("revenue_from_operations") for x in last]
    prof = [x.get("net_profit") for x in last]
    if any(v is None for v in eps + rev + prof) or sum(rev) <= 0:  # type: ignore[arg-type]
        return None
    return {"eps": float(sum(eps)), "margin": float(sum(prof) / sum(rev)),  # type: ignore[arg-type]
            "period_end": float(date.fromisoformat(sorted(q)[-1]).toordinal())}


def value_earnings_yield(as_of: date, names: list[str], d: Data,
                         max_age_days: int = 200) -> dict[str, float]:
    out = {}
    for n in names:
        t = _ttm(d.fundamentals(n, as_of))
        c = d.closes(n, as_of, 1)
        if t and len(c) and as_of.toordinal() - t["period_end"] <= max_age_days and t["eps"] > 0:
            out[n] = t["eps"] / float(c[-1])
    return out


def quality_margin(as_of: date, names: list[str], d: Data,
                   max_age_days: int = 200) -> dict[str, float]:
    out = {}
    for n in names:
        t = _ttm(d.fundamentals(n, as_of))
        if t and as_of.toordinal() - t["period_end"] <= max_age_days:
            out[n] = t["margin"]
    return out


def _z(x: dict[str, float]) -> dict[str, float]:
    v = np.array(list(x.values()))
    if len(v) < 5 or v.std() == 0:
        return {}
    return {k: float((val - v.mean()) / v.std()) for k, val in x.items()}


def composite(as_of: date, names: list[str], d: Data) -> dict[str, float]:
    """Equal-weight z-scores of momentum, value, low volatility and quality; a stock needs at
    least two components (missing components are skipped, not set to zero)."""
    parts = [_z(momentum_12_1(as_of, names, d)), _z(value_earnings_yield(as_of, names, d)),
             _z(low_volatility(as_of, names, d)), _z(quality_margin(as_of, names, d))]
    out = {}
    for n in names:
        have = [p[n] for p in parts if n in p]
        if len(have) >= 2:
            out[n] = float(np.mean(have))
    return out


SIGNALS: dict[str, Signal] = {
    "momentum_12_1": momentum_12_1, "trend_sma200": trend_sma200, "reversal_1m": reversal_1m,
    "low_volatility": low_volatility, "value_earnings_yield": value_earnings_yield,
    "quality_margin": quality_margin, "composite": composite,
}


@dataclass(frozen=True)
class Family:
    code: str
    name: str
    status: str
    required_data: tuple[str, ...]
    implementation: str | None
    validation: str
    limitations: str
    blocked_by: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()
    default_config: dict[str, Any] = field(default_factory=dict)


BT = "point-in-time liquid universe, next-session execution, Indian costs, walk-forward, " \
     "deflated Sharpe"
FAMILIES: tuple[Family, ...] = (
    Family("MOMENTUM_TREND", "Momentum / trend following", "RESEARCH",
           ("daily total-return prices",), "backtests: momentum_12_1, trend_sma200", BT,
           "daily bars only; momentum crashes and crowding are not modelled",
           signals=("momentum_12_1", "trend_sma200")),
    Family("MEAN_REVERSION", "Mean reversion", "RESEARCH", ("daily prices",),
           "backtests: reversal_1m", BT, "no intraday mean reversion (no intraday data)",
           signals=("reversal_1m",)),
    Family("STAT_ARB", "Statistical arbitrage", "DATA_BLOCKED",
           ("synchronised prices", "borrow availability and cost"), None,
           "cointegration with out-of-sample half-life checks",
           "short legs need SLB/borrow data", ("borrow data",)),
    Family("VALUE", "Value / fundamental", "RESEARCH",
           ("point-in-time results (filing dates)", "prices"),
           "backtests: value_earnings_yield", BT,
           "NSE results XBRL has the income statement only (no book value, cash flow or debt)",
           signals=("value_earnings_yield",)),
    Family("FACTOR", "Factor investing", "RESEARCH", ("prices", "point-in-time fundamentals"),
           "backtests: composite (momentum, value, low volatility, quality)", BT,
           "four factors; no sector neutralisation in the backtest",
           signals=("composite", "low_volatility", "quality_margin")),
    Family("EVENT_DRIVEN", "Event-driven", "PLANNED",
           ("timestamped events", "corporate actions"), None,
           "event study around announcement times",
           "the lake has NSE event calendars and ex-dates; an event study is not built"),
    Family("VOLATILITY_OPTIONS", "Volatility / options", "DATA_BLOCKED",
           ("option chains with OI/volume", "rates", "dividends"), None,
           "pricing only from reliable, traded quotes",
           "the lake holds NSE F&O and option greeks, but options analytics are not enabled; "
           "no production readiness claimed", ("options analytics not enabled",)),
    Family("RELATIVE_VALUE", "Relative value", "PLANNED", ("comparable prices", "fundamentals"),
           None, "peer-relative spreads, walk-forward", "not built"),
    Family("MARKET_MAKING", "Market making / microstructure", "DATA_BLOCKED",
           ("tick-level order book", "exchange connectivity"), None, "tick replay",
           "no tick data, no live execution; not production ready", ("tick data", "broker")),
    Family("EXECUTION_ALGOS", "VWAP / TWAP execution", "DATA_BLOCKED",
           ("intraday bars or ticks", "volume profiles"), None, "TCA against arrival / VWAP",
           "needs intraday data", ("intraday data",)),
    Family("MACRO_CROSS_ASSET", "Macro / cross-asset", "DATA_BLOCKED",
           ("macro series with release-date vintages", "cross-asset prices"), None,
           "release-time aligned backtests", "macro values are latest revisions, not vintages",
           ("macro vintages",)),
    Family("CARRY_YIELD", "Carry / yield", "PLANNED", ("dividend yields", "futures", "rates"),
           None, "carry decomposition", "not built"),
    Family("SEASONAL_CALENDAR", "Seasonal / calendar", "PLANNED",
           ("long daily history", "exchange calendars"), None,
           "multiple-testing-adjusted seasonal tests", "not built; very high overfitting risk"),
    Family("NEWS_SENTIMENT", "News / sentiment", "DATA_BLOCKED", ("licensed news",), None,
           "time-aligned sentiment vs baselines", "no licensed news feed; LLM text is not a "
           "signal", ("licensed news",)),
    Family("ML_DL", "Machine learning / deep learning", "RESEARCH",
           ("point-in-time features", "matured labels"),
           "model registry (/models): ridge, logistic, gradient boosting vs baselines",
           "purged + embargoed walk-forward, baselines, 7 gates incl. calibration, four-eyes "
           "approval, drift (PSI), realised-outcome monitoring, rollback",
           "deep learning is not used until simple models are beaten on real data; no model is "
           "used for decisions until approved"),
    Family("PORTFOLIO_OPTIMIZATION", "Portfolio optimisation / risk parity", "RESEARCH",
           ("covariance from adjusted returns",),
           "portfolio construction (/portfolio-construction); inverse-volatility weighting in "
           "backtests", "constraints re-checked after rounding; stress tests",
           "estimates change; shrinkage reduces but does not remove estimation error",
           signals=("low_volatility",)),
    Family("REGIME_SWITCHING", "Regime switching", "PLANNED", ("prices", "volatility"), None,
           "out-of-sample regime labels", "not built"),
    Family("INDEX_FUTURES_BASIS", "Index / futures / basis", "DATA_BLOCKED",
           ("futures with expiries", "index levels", "rates"), None, "basis and roll backtests",
           "the lake holds NSE F&O futures; not enabled in the platform",
           ("futures analytics not enabled",)),
    Family("FIXED_INCOME", "Fixed income / capital structure", "DATA_BLOCKED",
           ("bond prices", "curves", "ratings"), None, "curve-relative backtests",
           "no bond price history; not production ready", ("bond price history",)),
    Family("DIGITAL_ASSETS", "Digital assets", "DISABLED", ("venue data", "custody"), None,
           "as other families", "disabled; not production ready"),
)


def library() -> list[dict[str, Any]]:
    return [asdict(f) for f in FAMILIES]


def family_of(signal: str) -> str:
    for f in FAMILIES:
        if signal in f.signals:
            return f.code
    raise KeyError(signal)


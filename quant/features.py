"""Per-stock features from daily bars and point-in-time fundamentals (pure, deterministic).

Conventions
-----------
* Inputs are bars ON OR BEFORE the decision cutoff only; nothing here can see later data.
* Prices are total-return adjusted (splits, bonuses, dividends) for return features. Traded
  value is unadjusted (it is money, not a price).
* Trading-day windows: 1M = 21, 3M = 63, 6M = 126, 12M = 252.
* Annualisation: sqrt(252) for daily volatility.
* A feature that cannot be computed (short history, zero prices, missing benchmark) is None.
  It is never filled with a neutral value.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
from numpy.typing import NDArray

F = NDArray[np.float64]
FEATURE_VERSION = "features_v1"
TD_1M, TD_3M, TD_6M, TD_12M = 21, 63, 126, 252
ANN = math.sqrt(252)


@dataclass(frozen=True)
class Bars:
    dates: list[date]
    close: F  # total-return adjusted
    high: F
    low: F
    traded_value: F  # unadjusted INR

    @classmethod
    def from_rows(cls, rows: Sequence[dict[str, Any]]) -> Bars:
        rows = [r for r in rows if r.get("close") and r["close"] > 0]
        return cls([date.fromisoformat(str(r["trade_date"])[:10]) for r in rows],
                   np.array([float(r["close"]) for r in rows]),
                   np.array([float(r.get("high") or r["close"]) for r in rows]),
                   np.array([float(r.get("low") or r["close"]) for r in rows]),
                   np.array([float(r.get("traded_value") or 0.0) for r in rows]))

    def __len__(self) -> int:
        return len(self.close)


def _ret(c: F, back: int, skip: int = 0) -> float | None:
    if len(c) <= back:
        return None
    return float(c[-1 - skip] / c[-1 - back] - 1)


def price_features(b: Bars, bench: dict[date, float] | None = None) -> dict[str, float | None]:
    c = b.close
    n = len(c)
    out: dict[str, float | None] = {
        "RETURN_1M": _ret(c, TD_1M), "RETURN_3M": _ret(c, TD_3M), "RETURN_6M": _ret(c, TD_6M),
        # 12-1 momentum skips the latest month (short-term reversal)
        "MOMENTUM_12_1": _ret(c, TD_12M, skip=TD_1M) if n > TD_12M else None,
        "HISTORY_DAYS": float(n),
    }
    win = c[-TD_12M:] if n >= TD_12M else None
    out["PCT_OF_52W_HIGH"] = float(c[-1] / win.max()) if win is not None else None
    out["DIST_SMA200"] = float(c[-1] / c[-200:].mean() - 1) if n >= 200 else None
    lr = np.diff(np.log(c[-(TD_12M + 1):])) if n > TD_12M else None
    if lr is not None and len(lr) >= TD_12M:
        out["VOLATILITY_1Y"] = float(lr.std(ddof=1) * ANN)
        neg = lr[lr < 0]
        out["DOWNSIDE_VOL_1Y"] = float(math.sqrt((neg ** 2).sum() / len(lr)) * ANN)
        path = c[-(TD_12M + 1):]
        out["MAX_DRAWDOWN_1Y"] = float((path / np.maximum.accumulate(path) - 1).min())
    else:
        out["VOLATILITY_1Y"] = out["DOWNSIDE_VOL_1Y"] = out["MAX_DRAWDOWN_1Y"] = None
    tv = b.traded_value[-TD_1M:]
    out["ADV_VALUE_20D"] = float(np.median(tv)) if len(tv) >= 10 else None
    out["ZERO_VOLUME_SHARE_3M"] = (float((b.traded_value[-TD_3M:] <= 0).mean())
                                   if n >= TD_3M else None)
    out["BETA_1Y"] = _beta(b, bench) if bench else None
    return out


def _beta(b: Bars, bench: dict[date, float]) -> float | None:
    pairs = [(d, p) for d, p in zip(b.dates[-(TD_12M + 1):], b.close[-(TD_12M + 1):], strict=True)
             if d in bench]
    if len(pairs) < 120:  # needs about half a year of common days
        return None
    s = np.array([p for _, p in pairs])
    m = np.array([bench[d] for d, _ in pairs])
    rs, rm = np.diff(np.log(s)), np.diff(np.log(m))
    var = rm.var(ddof=1)
    return float(np.cov(rs, rm, ddof=1)[0, 1] / var) if var > 0 else None


def fundamental_features(rows: Sequence[dict[str, Any]], last_close: float | None,
                         as_of: date) -> tuple[dict[str, float | None], dict[str, Any]]:
    """From NSE quarterly results filed on or before ``as_of`` (the lake filters by filing time).
    Consolidated figures are preferred per period; standalone fills in when absent.
    Returns (features, provenance)."""
    q: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("period_type") != "Quarterly" or not r.get("to_date"):
            continue
        key = str(r["to_date"])[:10]
        if key not in q or r.get("consolidated") == "Consolidated":
            q[key] = r
    periods = sorted(q)
    feats: dict[str, float | None] = {k: None for k in (
        "REVENUE_GROWTH_YOY", "EPS_GROWTH_YOY", "NET_MARGIN_TTM", "EARNINGS_YIELD_TTM",
        "EPS_TTM")}
    prov: dict[str, Any] = {"latest_period_end": periods[-1] if periods else None,
                            "latest_filed": None, "quarters": len(periods)}
    if not periods:
        return feats, prov
    last = q[periods[-1]]
    prov["latest_filed"] = str(last.get("filing_datetime") or "")[:19] or None
    prov["basis"] = last.get("consolidated")
    year_ago = _shift_year(periods[-1])
    if year_ago in q:
        feats["REVENUE_GROWTH_YOY"] = _growth(last.get("revenue_from_operations"),
                                              q[year_ago].get("revenue_from_operations"))
        feats["EPS_GROWTH_YOY"] = _growth(last.get("eps_basic"), q[year_ago].get("eps_basic"))
    last4 = [q[p] for p in periods[-4:]]
    if len(last4) == 4 and _consecutive(periods[-4:]):
        rev = [x.get("revenue_from_operations") for x in last4]
        np_ = [x.get("net_profit") for x in last4]
        eps = [x.get("eps_basic") for x in last4]
        if all(v is not None for v in rev + np_) and sum(rev) > 0:  # type: ignore[arg-type]
            feats["NET_MARGIN_TTM"] = float(sum(np_) / sum(rev))  # type: ignore[arg-type]
        if all(v is not None for v in eps):
            ttm = float(sum(eps))  # type: ignore[arg-type]
            feats["EPS_TTM"] = ttm
            if last_close and last_close > 0:
                feats["EARNINGS_YIELD_TTM"] = ttm / last_close  # negative = loss-making
    return feats, prov


def _shift_year(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{int(y) - 1}-{m}-{d}"


def _consecutive(periods: list[str]) -> bool:
    ds = [date.fromisoformat(p) for p in periods]
    return all(80 <= (b - a).days <= 100 for a, b in itertools.pairwise(ds))


def _growth(now: Any, before: Any) -> float | None:
    if now is None or before is None or before <= 0:
        return None  # growth from a loss or zero base is not meaningful
    return float(now / before - 1)

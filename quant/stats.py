"""Performance and risk statistics from a daily price series (pure).

  returns          simple daily returns r_t = P_t / P_{t-1} - 1
  CAGR             (P_end / P_start)^(252 / days) - 1
  volatility       std(r, ddof=1) * sqrt(252)
  Sharpe           (mean(r) - rf/252) / std(r) * sqrt(252)       (rf annual, default 0)
  Sortino          (mean(r) - rf/252) / downside_dev * sqrt(252)
                   downside_dev = sqrt(mean(min(r, 0)^2))
  max drawdown     min over t of P_t / max_{s<=t} P_s - 1
  Calmar           CAGR / |max drawdown|
  beta, alpha      OLS of r on benchmark returns; alpha annualised (daily intercept * 252)
  correlation      Pearson of daily returns
  VaR / ES (95%)   historical, one day: 5th percentile loss and mean loss beyond it (positive)
Sharpe/Sortino assume i.i.d. returns; serial correlation and fat tails make them optimistic.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import ArrayLike

ANN = 252


def returns(p: ArrayLike) -> np.ndarray:
    x = np.asarray(p, dtype=float)
    out: np.ndarray = x[1:] / x[:-1] - 1
    return out


def summary(p: ArrayLike, bench: ArrayLike | None = None, rf_annual: float = 0.0,
            min_obs: int = 60) -> dict[str, Any]:
    x = np.asarray(p, dtype=float)
    out: dict[str, Any] = {"observations": len(x)}
    if len(x) <= min_obs:
        out["status"] = "INSUFFICIENT DATA"
        return out
    r = returns(x)
    ex = r - rf_annual / ANN
    sd = r.std(ddof=1)
    down = math.sqrt(float(np.mean(np.minimum(r, 0) ** 2)))
    cagr = (x[-1] / x[0]) ** (ANN / len(r)) - 1
    dd = float((x / np.maximum.accumulate(x) - 1).min())
    q = float(np.quantile(r, 0.05))
    tail = r[r <= q]
    out |= {"status": "OK", "total_return": float(x[-1] / x[0] - 1), "cagr": float(cagr),
            "volatility": float(sd * math.sqrt(ANN)),
            "sharpe": float(ex.mean() / sd * math.sqrt(ANN)) if sd > 0 else None,
            "sortino": float(ex.mean() / down * math.sqrt(ANN)) if down > 0 else None,
            "max_drawdown": dd, "calmar": float(cagr / abs(dd)) if dd < 0 else None,
            "var_95_1d": float(-q), "es_95_1d": float(-tail.mean()) if len(tail) else None,
            "best_day": float(r.max()), "worst_day": float(r.min()),
            "positive_days": float((r > 0).mean())}
    if bench is not None:
        b = np.asarray(bench, dtype=float)
        if len(b) == len(x):
            rb = returns(b)
            var = rb.var(ddof=1)
            if var > 0:
                beta = float(np.cov(r, rb, ddof=1)[0, 1] / var)
                out |= {"beta": beta,
                        "alpha_annual": float((r.mean() - beta * rb.mean()) * ANN),
                        "correlation": float(np.corrcoef(r, rb)[0, 1]),
                        "tracking_error": float((r - rb).std(ddof=1) * math.sqrt(ANN))}
    return out


def rolling_volatility(p: ArrayLike, window: int = 21) -> np.ndarray:
    r = returns(p)
    out = np.full(len(r), np.nan)
    for i in range(window - 1, len(r)):
        out[i] = r[i - window + 1:i + 1].std(ddof=1) * math.sqrt(ANN)
    return out

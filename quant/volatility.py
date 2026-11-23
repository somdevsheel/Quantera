"""Realised-volatility estimators from daily OHLC (pure; annualised with 252 days).

Let o, h, l, c be logs of open, high, low, close and c1 the previous close (log).
  Close-to-close  std of (c - c1), sample (ddof = 1)
  Parkinson       sqrt( mean((h - l)^2) / (4 ln 2) )                    uses the range only
  Garman-Klass    sqrt( mean(0.5 (h - l)^2 - (2 ln 2 - 1)(c - o)^2) )   assumes no drift, no gaps
  Rogers-Satchell sqrt( mean((h - c)(h - o) + (l - c)(l - o)) )         allows drift, no gaps
  Yang-Zhang      sqrt( s_o^2 + k s_c^2 + (1 - k) s_rs^2 ),  k = 0.34 / (1.34 + (n+1)/(n-1))
                  s_o^2 = var(o - c1) (overnight), s_c^2 = var(c - o) (open-to-close),
                  s_rs^2 = Rogers-Satchell variance; handles drift AND opening gaps.
Assumptions: continuous trading within the day; prices adjusted for splits; Parkinson and
Garman-Klass understate volatility when prices gap overnight (Indian equities do).
Missing data: bars with non-positive prices are dropped; fewer than ``min_obs`` bars -> None.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import ArrayLike

ANN = 252


def _logs(o: ArrayLike, h: ArrayLike, lo: ArrayLike, c: ArrayLike
          ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    arr = np.vstack([np.asarray(x, dtype=float) for x in (o, h, lo, c)])
    keep = np.all(arr > 0, axis=0)
    lo_, hi, lw, cl = (np.log(a[keep]) for a in arr)
    return lo_, hi, lw, cl


def close_to_close(c: ArrayLike, min_obs: int = 20) -> float | None:
    x = np.asarray(c, dtype=float)
    x = x[x > 0]
    if len(x) <= min_obs:
        return None
    return float(np.diff(np.log(x)).std(ddof=1) * math.sqrt(ANN))


def parkinson(h: ArrayLike, lo: ArrayLike, min_obs: int = 20) -> float | None:
    hh, ll = np.log(np.asarray(h, dtype=float)), np.log(np.asarray(lo, dtype=float))
    if len(hh) < min_obs:
        return None
    return float(math.sqrt(np.mean((hh - ll) ** 2) / (4 * math.log(2)) * ANN))


def garman_klass(o: ArrayLike, h: ArrayLike, lo: ArrayLike, c: ArrayLike,
                 min_obs: int = 20) -> float | None:
    lo_, lh, ll, lc = _logs(o, h, lo, c)
    if len(lo_) < min_obs:
        return None
    v = np.mean(0.5 * (lh - ll) ** 2 - (2 * math.log(2) - 1) * (lc - lo_) ** 2)
    return float(math.sqrt(max(v, 0.0) * ANN))


def _rs_var(lo_: np.ndarray, lh: np.ndarray, ll: np.ndarray, lc: np.ndarray) -> float:
    return float(np.mean((lh - lc) * (lh - lo_) + (ll - lc) * (ll - lo_)))


def rogers_satchell(o: ArrayLike, h: ArrayLike, lo: ArrayLike, c: ArrayLike,
                    min_obs: int = 20) -> float | None:
    lo_, lh, ll, lc = _logs(o, h, lo, c)
    if len(lo_) < min_obs:
        return None
    return float(math.sqrt(max(_rs_var(lo_, lh, ll, lc), 0.0) * ANN))


def yang_zhang(o: ArrayLike, h: ArrayLike, lo: ArrayLike, c: ArrayLike,
               min_obs: int = 20) -> float | None:
    lo_, lh, ll, lc = _logs(o, h, lo, c)
    n = len(lo_) - 1  # the first bar only supplies a previous close
    if n < min_obs:
        return None
    overnight = lo_[1:] - lc[:-1]
    open_close = lc[1:] - lo_[1:]
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    var = (overnight.var(ddof=1) + k * open_close.var(ddof=1)
           + (1 - k) * _rs_var(lo_[1:], lh[1:], ll[1:], lc[1:]))
    return float(math.sqrt(max(var, 0.0) * ANN))


def all_estimators(o: ArrayLike, h: ArrayLike, lo: ArrayLike, c: ArrayLike,
                   window: int = 63) -> dict[str, float | None]:
    """Every estimator over the last ``window`` bars (default 3 months)."""
    s = slice(-window - 1, None)
    oo, hh, ll, cc = (np.asarray(x, dtype=float)[s] for x in (o, h, lo, c))
    return {"close_to_close": close_to_close(cc), "parkinson": parkinson(hh[1:], ll[1:]),
            "garman_klass": garman_klass(oo[1:], hh[1:], ll[1:], cc[1:]),
            "rogers_satchell": rogers_satchell(oo[1:], hh[1:], ll[1:], cc[1:]),
            "yang_zhang": yang_zhang(oo, hh, ll, cc), "window_days": float(window)}

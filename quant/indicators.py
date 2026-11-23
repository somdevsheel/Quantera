"""Technical indicators (pure numpy; arrays in time order; NaN during warm-up).

Formulas (Wilder smoothing = EMA with alpha = 1/n):
  SMA(n)          mean of the last n closes
  EMA(n)          alpha = 2/(n+1), seeded with SMA(n)
  RSI(n)          100 - 100/(1 + avgGain/avgLoss), Wilder averages (default n = 14)
  MACD            EMA(12) - EMA(26); signal = EMA(9) of MACD; histogram = MACD - signal
  Bollinger(n,k)  SMA(n) +/- k * population std of the last n closes (default 20, 2)
  ATR(n)          Wilder average of true range max(H-L, |H-C1|, |L-C1|) (default 14)
  ADX(n)          Wilder: +DI, -DI from directional movement; DX = 100|+DI - -DI|/(+DI + -DI);
                  ADX = Wilder average of DX (default 14)
  Stochastic      %K = 100 (C - LL_n)/(HH_n - LL_n) (n = 14); %D = SMA(3) of %K
Warm-up: the first value appears once the window is full; earlier values are NaN.
Corporate actions: feed split/bonus-adjusted (or total-return adjusted) bars, else gaps at
ex-dates distort every indicator.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

F = NDArray[np.float64]


def _a(x: ArrayLike) -> F:
    return np.asarray(x, dtype=float)


def sma(x: ArrayLike, n: int) -> F:
    v = _a(x)
    out = np.full(len(v), np.nan)
    if len(v) >= n:
        c = np.cumsum(np.insert(v, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def ema(x: ArrayLike, n: int, alpha: float | None = None) -> F:
    v = _a(x)
    out = np.full(len(v), np.nan)
    if len(v) < n:
        return out
    a = alpha if alpha is not None else 2.0 / (n + 1)
    out[n - 1] = v[:n].mean()
    for i in range(n, len(v)):
        out[i] = a * v[i] + (1 - a) * out[i - 1]
    return out


def wilder(x: ArrayLike, n: int) -> F:
    return ema(x, n, alpha=1.0 / n)


def rsi(close: ArrayLike, n: int = 14) -> F:
    c = _a(close)
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    d = np.diff(c)
    g, lo = wilder(np.clip(d, 0, None), n), wilder(np.clip(-d, 0, None), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(lo == 0, 100.0, 100 - 100 / (1 + g / lo))
    out[1:] = np.where(np.isnan(g), np.nan, r)
    return out


def macd(close: ArrayLike, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[F, F, F]:
    c = _a(close)
    line = ema(c, fast) - ema(c, slow)
    sig = np.full(len(c), np.nan)
    ok = np.flatnonzero(~np.isnan(line))
    if len(ok) >= signal:
        sig[ok] = ema(line[ok], signal)
    return line, sig, line - sig


def bollinger(close: ArrayLike, n: int = 20, k: float = 2.0) -> tuple[F, F, F]:
    c = _a(close)
    mid = sma(c, n)
    sd = np.full(len(c), np.nan)
    for i in range(n - 1, len(c)):
        sd[i] = c[i - n + 1:i + 1].std()
    return mid - k * sd, mid, mid + k * sd


def true_range(high: ArrayLike, low: ArrayLike, close: ArrayLike) -> F:
    h, lo, c = _a(high), _a(low), _a(close)
    prev = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - lo, np.abs(h - prev), np.abs(lo - prev)]), axis=0)
    return np.asarray(tr, dtype=float)


def atr(high: ArrayLike, low: ArrayLike, close: ArrayLike, n: int = 14) -> F:
    return wilder(true_range(high, low, close), n)


def adx(high: ArrayLike, low: ArrayLike, close: ArrayLike, n: int = 14) -> tuple[F, F, F]:
    h, lo = _a(high), _a(low)
    up, dn = np.r_[np.nan, np.diff(h)], np.r_[np.nan, -np.diff(lo)]
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    plus[0] = minus[0] = 0.0
    tr = wilder(true_range(h, lo, close), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * wilder(plus, n) / tr
        mdi = 100 * wilder(minus, n) / tr
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    out = np.full(len(h), np.nan)
    ok = np.flatnonzero(np.isfinite(dx))
    if len(ok) >= n:
        out[ok] = wilder(dx[ok], n)
    return out, pdi, mdi


def stochastic(high: ArrayLike, low: ArrayLike, close: ArrayLike, n: int = 14,
               d: int = 3) -> tuple[F, F]:
    h, lo, c = _a(high), _a(low), _a(close)
    k = np.full(len(c), np.nan)
    for i in range(n - 1, len(c)):
        hh, ll = h[i - n + 1:i + 1].max(), lo[i - n + 1:i + 1].min()
        k[i] = 100 * (c[i] - ll) / (hh - ll) if hh > ll else np.nan
    return k, sma(np.nan_to_num(k, nan=np.nan), d)


def support_resistance(high: ArrayLike, low: ArrayLike, n: int = 20) -> tuple[float, float]:
    """Lowest low and highest high of the last n bars (simple, documented levels)."""
    h, lo = _a(high)[-n:], _a(low)[-n:]
    return float(lo.min()), float(h.max())

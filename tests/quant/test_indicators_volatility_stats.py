"""Reference tests: hand-computed values and statistical properties with known answers."""

from __future__ import annotations

import math

import numpy as np
import pytest

from quant import indicators as ind
from quant import stats
from quant import volatility as vol


def test_sma_ema_warmup_and_values():
    x = np.arange(1, 11, dtype=float)
    s = ind.sma(x, 3)
    assert np.isnan(s[:2]).all() and s[2] == 2.0 and s[-1] == 9.0
    e = ind.ema(x, 3)
    assert e[2] == 2.0 and e[3] == pytest.approx(0.5 * 4 + 0.5 * 2.0)


def test_rsi_extremes_and_range():
    up = np.arange(1, 40, dtype=float)
    assert ind.rsi(up)[-1] == 100.0
    rng = np.random.default_rng(0)
    r = ind.rsi(100 + np.cumsum(rng.normal(0, 1, 300)))
    ok = r[np.isfinite(r)]
    assert np.isnan(r[:14]).all() and (ok >= 0).all() and (ok <= 100).all()


def test_rsi_wilder_reference():
    # Wilder's classic example gains/losses produce RSI ~ 70.46 after 14 periods
    c = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03,
         45.61, 46.28, 46.28]
    assert ind.rsi(c)[14] == pytest.approx(70.46, abs=0.05)


def test_macd_and_bollinger():
    rng = np.random.default_rng(1)
    c = 100 + np.cumsum(rng.normal(0, 1, 200))
    line, sig, hist = ind.macd(c)
    np.testing.assert_allclose(hist[~np.isnan(hist)], (line - sig)[~np.isnan(hist)])
    _lo, mid, hi = ind.bollinger(c)
    assert mid[-1] == pytest.approx(c[-20:].mean())
    assert hi[-1] - mid[-1] == pytest.approx(2 * c[-20:].std())


def test_atr_adx_stochastic():
    h = np.array([10, 11, 12, 11, 13] * 10, dtype=float)
    lo = h - 1
    c = h - 0.5
    tr = ind.true_range(h, lo, c)
    assert tr[0] == 1.0 and tr[1] == pytest.approx(max(1, abs(11 - 9.5), abs(10 - 9.5)))
    a = ind.atr(h, lo, c)
    assert np.isnan(a[:13]).all() and a[-1] > 0
    trend = np.arange(100, 200, dtype=float)
    adx, pdi, mdi = ind.adx(trend + 1, trend - 1, trend)
    assert adx[-1] > 50 and pdi[-1] > mdi[-1]  # strong up-trend
    k, _d = ind.stochastic(h, lo, c)
    assert np.nanmin(k) >= 0 and np.nanmax(k) <= 100
    assert ind.support_resistance(h, lo, 5) == (float(lo[-5:].min()), float(h[-5:].max()))


def _gbm(sigma, n=1000, seed=0):
    rng = np.random.default_rng(seed)
    steps = 375  # one step per minute of the NSE session, so highs/lows are realistic
    r = rng.normal(0, sigma / math.sqrt(252 * steps), (n, steps))
    path = 100 * np.exp(np.cumsum(r.ravel())).reshape(n, steps)
    o, c = path[:, 0], path[:, -1]
    return o, path.max(axis=1), path.min(axis=1), c


def test_estimators_recover_true_volatility():
    o, h, lo, c = _gbm(0.30)
    for est in (vol.close_to_close(c), vol.garman_klass(o, h, lo, c),
                vol.rogers_satchell(o, h, lo, c), vol.yang_zhang(o, h, lo, c)):
        assert est == pytest.approx(0.30, rel=0.12)
    # discrete sampling makes Parkinson read low; it must still be in the right range
    assert 0.2 < vol.parkinson(h, lo) < 0.32


def test_estimators_need_data():
    assert vol.yang_zhang([1, 2], [1, 2], [1, 2], [1, 2]) is None
    assert vol.close_to_close([100.0] * 5) is None
    out = vol.all_estimators(*_gbm(0.2, n=200))
    assert set(out) >= {"parkinson", "garman_klass", "rogers_satchell", "yang_zhang"}


def test_summary_reference_values():
    p = np.array([100 * 1.001 ** i for i in range(253)])
    s = stats.summary(p)
    assert s["cagr"] == pytest.approx(1.001 ** 252 - 1)
    assert s["max_drawdown"] == pytest.approx(0.0) and s["volatility"] == pytest.approx(0, abs=1e-9)
    crash = np.r_[np.linspace(100, 200, 100), np.linspace(200, 100, 100)]
    assert stats.summary(crash)["max_drawdown"] == pytest.approx(-0.5)
    rng = np.random.default_rng(3)
    m = 100 * np.cumprod(1 + rng.normal(0, 0.01, 500))
    s2 = stats.summary(m * 1.0, m)
    assert s2["beta"] == pytest.approx(1.0) and s2["correlation"] == pytest.approx(1.0)
    assert s2["var_95_1d"] > 0 and s2["es_95_1d"] >= s2["var_95_1d"]
    assert stats.summary(p[:30])["status"] == "INSUFFICIENT DATA"

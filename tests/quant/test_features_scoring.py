"""Features and decision engine: reference values, no look-ahead, honest missing data."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from quant import features as fx
from quant import scoring as sc

D0 = date(2024, 1, 1)


def bars(closes, value=1e9):
    n = len(closes)
    dates = [D0 + timedelta(days=i) for i in range(n)]
    c = np.asarray(closes, dtype=float)
    return fx.Bars(dates, c, c * 1.01, c * 0.99, np.full(n, value))


def test_returns_and_momentum_reference_values():
    c = np.linspace(100, 200, 300)
    f = fx.price_features(bars(c))
    assert f["RETURN_1M"] == pytest.approx(c[-1] / c[-22] - 1)
    assert f["MOMENTUM_12_1"] == pytest.approx(c[-22] / c[-253] - 1)  # skips the last month
    assert f["PCT_OF_52W_HIGH"] == pytest.approx(1.0)
    assert f["MAX_DRAWDOWN_1Y"] == pytest.approx(0.0)
    assert f["HISTORY_DAYS"] == 300


def test_volatility_and_drawdown():
    rng = np.random.default_rng(0)
    r = rng.normal(0, 0.01, 400)
    c = 100 * np.exp(np.cumsum(r))
    f = fx.price_features(bars(c))
    assert f["VOLATILITY_1Y"] == pytest.approx(np.diff(np.log(c[-253:])).std(ddof=1) * np.sqrt(252))
    assert f["MAX_DRAWDOWN_1Y"] <= 0
    crash = np.r_[np.full(300, 100.0), np.full(100, 50.0)]
    assert fx.price_features(bars(crash))["MAX_DRAWDOWN_1Y"] == pytest.approx(-0.5)


def test_short_history_is_none_not_zero():
    f = fx.price_features(bars(np.linspace(100, 110, 60)))
    assert f["MOMENTUM_12_1"] is None and f["VOLATILITY_1Y"] is None and f["DIST_SMA200"] is None


def test_beta_against_benchmark():
    rng = np.random.default_rng(1)
    m = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))
    s = m ** 1.5  # log returns exactly 1.5x the market
    b = bars(s)
    bench = dict(zip(b.dates, m, strict=True))
    assert fx.price_features(b, bench)["BETA_1Y"] == pytest.approx(1.5, rel=1e-6)


def test_no_look_ahead_in_price_features():
    rng = np.random.default_rng(2)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 400)))
    base = fx.price_features(bars(c[:350]))
    changed = c.copy()
    changed[350:] *= 3  # future prices change...
    assert fx.price_features(bars(changed[:350])) == base  # ...features at the cutoff do not


def _q(end, rev, np_, eps, cons="Consolidated"):
    return {"period_type": "Quarterly", "to_date": end, "consolidated": cons,
            "revenue_from_operations": rev, "net_profit": np_, "eps_basic": eps,
            "filing_datetime": end + "T18:00:00"}


def test_fundamental_features_ttm_growth_and_preference():
    rows = [_q("2024-06-30", 100, 10, 1.0), _q("2024-09-30", 110, 11, 1.1),
            _q("2024-12-31", 120, 12, 1.2), _q("2025-03-31", 130, 13, 1.3),
            _q("2025-06-30", 150, 15, 1.5), _q("2025-06-30", 999, 99, 9.9, "Non-Consolidated")]
    f, prov = fx.fundamental_features(rows, last_close=51.0, as_of=date(2025, 8, 1))
    assert f["REVENUE_GROWTH_YOY"] == pytest.approx(150 / 100 - 1)  # consolidated preferred
    assert f["EPS_TTM"] == pytest.approx(1.1 + 1.2 + 1.3 + 1.5)
    assert f["EARNINGS_YIELD_TTM"] == pytest.approx(5.1 / 51.0)
    assert f["NET_MARGIN_TTM"] == pytest.approx((11 + 12 + 13 + 15) / (110 + 120 + 130 + 150))
    assert prov["latest_period_end"] == "2025-06-30"


def test_growth_from_loss_base_is_none():
    rows = [_q("2024-06-30", 100, -5, -0.5), _q("2025-06-30", 120, 5, 0.5)]
    f, _ = fx.fundamental_features(rows, 10.0, date(2025, 8, 1))
    assert f["EPS_GROWTH_YOY"] is None and f["REVENUE_GROWTH_YOY"] == pytest.approx(0.2)
    assert f["EPS_TTM"] is None  # four consecutive quarters are required


# --------------------------------------------------------------------------- scoring
AS_OF = date(2025, 8, 1)


def row(i, mom=0.0, vol=0.2, margin=0.1, growth=0.1, ey=0.05, adv=1e9, fresh=True,
        price_as_of=AS_OF):
    return {"instrument_id": f"IN:{i}", "symbol": f"S{i}", "price_as_of": price_as_of,
            "fundamentals_period_end": date(2025, 6, 30) if fresh else date(2023, 6, 30),
            "features": {"MOMENTUM_12_1": mom, "RETURN_6M": mom, "PCT_OF_52W_HIGH": 0.9 + mom / 10,
                         "DIST_SMA200": mom / 2, "VOLATILITY_1Y": vol, "DOWNSIDE_VOL_1Y": vol,
                         "MAX_DRAWDOWN_1Y": -vol, "BETA_1Y": 1.0, "NET_MARGIN_TTM": margin,
                         "REVENUE_GROWTH_YOY": growth, "EPS_GROWTH_YOY": growth,
                         "EARNINGS_YIELD_TTM": ey, "ADV_VALUE_20D": adv, "HISTORY_DAYS": 500,
                         "ZERO_VOLUME_SHARE_3M": 0.0}}


def test_percentiles_ties_and_too_few_peers():
    assert sc.percentiles([1, 2, 3], 1) == [None] * 3  # fewer than MIN_PEERS
    p = sc.percentiles([1, 2, 2, 3, 4, None], 1)
    assert p[0] == 0 and p[4] == 100 and p[1] == p[2] and p[5] is None
    assert sc.percentiles([5, 5, 5, 5, 5], 1) == [None] * 5  # no variation


def test_best_stock_is_buy_candidate_and_order_is_stable():
    rows = [row(i, mom=i / 10, margin=i / 50, growth=i / 20, ey=0.01 * (i + 1), vol=0.4 - i / 40)
            for i in range(10)]
    out = sc.score(rows, as_of=AS_OF)
    assert out[0].symbol == "S9" and out[0].state == "BUY CANDIDATE"
    assert out[0].coverage == "COMPLETE" and out[0].supporting
    assert out[-1].state in ("NO TRADE", "AVOID")
    assert [d.state for d in out] == sorted((d.state for d in out), key=sc.STATE_ORDER.index)


def test_stale_fundamentals_cap_at_wait_and_are_not_neutral():
    rows = [row(i, mom=i / 10, vol=0.4 - i / 40, fresh=False) for i in range(10)]
    out = {d.symbol: d for d in sc.score(rows, as_of=AS_OF)}
    best = out["S9"]
    assert best.pillars["QUALITY"] is None and best.pillars["VALUATION"] is None
    assert best.state == "WAIT" and any("fundamental" in o for o in best.overrides)
    assert any("not used" in c for c in best.counter)


def test_risk_override_illiquidity_staleness_and_failures():
    rows = [row(i, mom=i / 10, margin=i / 50, growth=i / 20, ey=0.01 * (i + 1)) for i in range(9)]
    rows.append(row(9, mom=0.9, margin=0.2, growth=0.5, ey=0.1, vol=3.0))  # wildest stock
    rows.append(row(10, mom=0.95, margin=0.2, growth=0.5, ey=0.1, adv=1e5))  # illiquid
    rows.append(row(11, price_as_of=date(2025, 6, 1)))  # stale prices
    rows.append({"instrument_id": "IN:x", "symbol": "BROKEN", "failed": "lake timeout"})
    out = {d.symbol: d for d in sc.score(rows, as_of=AS_OF)}
    assert out["S9"].state == "AVOID" and "BLOCKED BY RISK" in out["S9"].overrides[0]
    assert out["S10"].state == "NO TRADE" and "liquidity" in out["S10"].overrides[0]
    assert out["S11"].state == "STALE DATA" and out["S11"].coverage == "STALE"
    assert out["BROKEN"].coverage == "FAILED" and out["BROKEN"].opportunity is None


def test_loss_maker_valuation_is_missing_and_flagged():
    rows = [row(i, mom=i / 10, ey=0.02 * (i + 1)) for i in range(9)] + [row(9, ey=-0.03)]
    out = {d.symbol: d for d in sc.score(rows, as_of=AS_OF)}
    assert out["S9"].pillars["VALUATION"] is None
    assert any("loss-making" in c for c in out["S9"].counter)

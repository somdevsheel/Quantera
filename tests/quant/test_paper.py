"""Paper order simulation."""

from __future__ import annotations

from datetime import date

import pytest

from quant import paper as pp

D = date(2025, 3, 3)


def bar(o, h, lo, c, v=1e6):
    return pp.DayBar(D, o, h, lo, c, v)


def test_market_fills_at_open_with_slippage():
    o = pp.SimOrder("BUY", 100, 0, "MARKET")
    r = pp.step(o, bar(100, 102, 99, 101))
    assert r.fill.price == pytest.approx(100 * 1.0005) and o.remaining == 0


def test_limit_buy_and_sell():
    b = pp.SimOrder("BUY", 10, 0, "LIMIT", limit=98)
    assert pp.step(b, bar(100, 101, 97, 99)).fill.price == 98
    s = pp.SimOrder("SELL", 10, 0, "LIMIT", limit=105, tif="GTC")
    assert pp.step(s, bar(100, 104, 99, 102)).fill is None
    assert pp.step(s, bar(106, 107, 105, 106)).fill.price == 106  # opened above: at the open


def test_stop_gap_and_stop_limit_non_fill():
    st = pp.SimOrder("SELL", 10, 0, "STOP", stop=95, tif="GTC")
    r = pp.step(st, bar(90, 92, 89, 91))
    assert r.triggered_now and r.fill.price == pytest.approx(90 * 0.9995)
    sl = pp.SimOrder("SELL", 10, 0, "STOP_LIMIT", stop=95, limit=94, tif="GTC")
    r = pp.step(sl, bar(90, 92, 89, 91))
    assert r.triggered_now and r.fill is None  # gapped through the limit
    assert pp.step(sl, bar(93, 94.5, 92, 94)).fill.price == 94


def test_participation_cap_partial_and_day_expiry():
    o = pp.SimOrder("BUY", 1000, 0, "MARKET", tif="DAY")
    r = pp.step(o, bar(100, 101, 99, 100, v=5000))
    assert r.fill.quantity == 500 and "participation" in r.fill.note and r.expired
    g = pp.SimOrder("BUY", 10, 0, "LIMIT", limit=50, tif="GTC")
    for _ in range(89):
        assert not pp.step(g, bar(100, 101, 99, 100), gtc_sessions=90).expired
    assert pp.step(g, bar(100, 101, 99, 100), gtc_sessions=90).expired


def test_transitions():
    assert pp.can_move("NEW", "WORKING") and not pp.can_move("FILLED", "CANCELLED")
    assert pp.can_move("PARTIALLY_FILLED", "FILLED")

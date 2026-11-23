"""Stop / target / trailing engine: direction, rounding, gaps, OCO, edits, panel."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from quant import protection as pr

D = date(2025, 1, 1)


def st(**kw):
    base = dict(side="LONG", entry=100.0, quantity=10, initial_stop=95.0, stop=95.0,
                targets=[pr.Leg(105.0, 0.5), pr.Leg(110.0, 0.5)], approach_fraction=0.0)
    return pr.PlanState(**(base | kw))


def bar(i, o, h, lo, c):
    return pr.Bar(D + timedelta(days=i), o, h, lo, c)


def test_rounding_never_adds_risk():
    assert pr.round_level(94.97, 0.05, "LONG", "stop") == 95.0  # long stop rounds up
    assert pr.round_level(105.03, 0.05, "SHORT", "stop") == 105.0  # short stop rounds down
    assert pr.round_level(110.04, 0.05, "LONG", "target") == 110.0  # target rounds towards ref
    assert pr.round_level(89.96, 0.05, "SHORT", "target") == 90.0


def test_initial_stop_methods_and_direction():
    c = np.linspace(90, 110, 60)
    h, lo = c + 1, c - 1
    s = pr.initial_stop("LONG", 110, c, h, lo, c, "ATR", 2.0)
    assert s["price"] < 110 and s["atr"] == pytest.approx(2.0, rel=0.2)
    assert pr.initial_stop("SHORT", 110, c, h, lo, c, "PERCENT", 0.05)["price"] == 115.5
    sw = pr.initial_stop("LONG", 110, c, h, lo, c, "SWING", 0)
    assert sw["price"] < lo[-20:].min()
    with pytest.raises(ValueError):
        pr.initial_stop("LONG", 100, c, h, lo, c, "FIXED_PRICE", 101)  # wrong side


def test_targets_rr_and_fractions():
    t, w = pr.targets("LONG", 100, 95, [{"method": "RISK_REWARD", "value": 2, "fraction": 0.5},
                                        {"method": "PERCENT", "value": 0.05, "fraction": 0.5}],
                      min_reward_risk=1.5)
    assert [x["price"] for x in t] == [105.0, 110.0] and w and "1.00R" in w[0]
    with pytest.raises(ValueError):
        pr.targets("LONG", 100, 95, [{"method": "PERCENT", "value": 0.1, "fraction": 0.7},
                                     {"method": "PERCENT", "value": 0.2, "fraction": 0.7}])


def test_oco_partial_then_stop_on_remainder():
    s = st()
    ev = pr.step(s, bar(1, 101, 106, 100, 104))
    assert [e.rule for e in ev if e.kind == "FILL"] == ["TARGET_1"] and s.remaining == 5
    ev = pr.step(s, bar(2, 103, 103, 94, 95))
    f = [e for e in ev if e.kind == "FILL"]
    assert f[0].rule == "STOP" and f[0].quantity == 5
    assert s.closed and s.targets[1].state == "CANCELLED"


def test_ambiguous_bar_takes_stop_and_short_side():
    s = st()
    ev = pr.step(s, bar(1, 100, 106, 94, 100))
    assert any(e.kind == "AMBIGUOUS_BAR" for e in ev)
    assert [e.rule for e in ev if e.kind == "FILL"] == ["STOP"]
    sh = st(side="SHORT", initial_stop=105.0, stop=105.0, targets=[pr.Leg(95.0, 1.0)])
    ev = pr.step(sh, bar(1, 99, 100, 94, 96))
    assert [(e.rule, e.price) for e in ev if e.kind == "FILL"] == [("TARGET_1", 95.0)]


def test_gaps_and_stop_limit_non_fill():
    s = st()
    f = next(e for e in pr.step(s, bar(1, 90, 91, 89, 90)) if e.kind == "FILL")
    assert f.rule == "STOP_GAP" and f.price == 90 and f.details["gap_loss"] == 50
    sl = st(stop_limit_offset=1.0)
    ev = pr.step(sl, bar(1, 90, 91, 89, 90))
    assert not any(e.kind == "FILL" for e in ev) and sl.stop_state == "PENDING"
    assert not any(e.kind == "FILL" for e in pr.step(sl, bar(2, 92, 93.5, 91, 93)))
    ev = pr.step(sl, bar(3, 93, 94.5, 92, 94))
    assert sl.closed and next(e for e in ev if e.kind == "FILL").price == 94.0
    tg = st()  # gap through a target fills at the target, not the better open
    ev = pr.step(tg, bar(1, 107, 108, 106, 107))
    assert [(e.rule, e.price) for e in ev if e.kind == "FILL"] == [("TARGET_1", 105.0)]


def test_trailing_tightens_only_from_next_bar():
    s = st(targets=[], trail={"method": "AMOUNT", "value": 3.0})
    pr.step(s, bar(1, 100, 104, 99.5, 103))
    assert s.stop == 101.0  # applies from the next bar
    pr.step(s, bar(2, 103, 103.5, 101.5, 102))
    assert s.stop == 101.0 and not s.closed  # a lower high never loosens it
    ev = pr.step(s, bar(3, 102, 102.5, 100, 100.5))
    assert [e.rule for e in ev if e.kind == "FILL"] == ["TRAIL"]
    atr = st(targets=[], trail={"method": "ATR", "value": 2.0})
    pr.step(atr, bar(1, 100, 104, 99, 103), atr_value=None)
    assert atr.trail_level is None  # ATR not ready: waits, never guesses


def test_breakeven_includes_costs_and_time_stop():
    be = pr.breakeven_price("LONG", 100, fee_per_share=0.1, slip_per_share=0.05)
    assert be == pytest.approx(100.3)
    s = st(breakeven_after_first_target=True, fee_per_share=0.1, slippage_bps=5)
    pr.step(s, bar(1, 101, 105.5, 100.5, 104))
    assert s.stop == pytest.approx(100.3)
    t = st(targets=[], time_stop_bars=2)
    pr.step(t, bar(1, 100, 101, 99, 100))
    ev = pr.step(t, bar(2, 100, 101, 99, 100.5))
    assert [e.rule for e in ev if e.kind == "FILL"] == ["TIME"]


def test_edits_panel_and_serialisation():
    s = st(trail={"method": "ATR", "value": 2.0})
    assert pr.is_risk_increase(s, new_stop=93.0) and not pr.is_risk_increase(s, new_stop=97.0)
    assert pr.is_risk_increase(s, new_trail={"method": "ATR", "value": 3.0})
    assert pr.is_risk_increase(s, new_trail={})
    with pytest.raises(ValueError):
        pr.validate_levels(st(stop=101.0), 100.0)
    p = pr.price_panel(100.0, s, draft=True)
    assert set(p) == {"current_price", "stop_loss", "target_price", "trailing_stop_loss"}
    assert p["stop_loss"]["state"] == "SUGGESTED" and p["trailing_stop_loss"]["state"] == "PENDING"
    assert pr.PlanState.from_json(s.to_json()) == s


def test_simulate_equals_stepping():
    rng = np.random.default_rng(0)
    c = 100 * np.cumprod(1 + rng.normal(0, 0.02, 80))
    bars = [bar(i, c[i - 1] if i else 100, c[i] * 1.01, c[i] * 0.99, c[i]) for i in range(80)]
    a, b = st(), st()
    sim = pr.simulate(a, bars)
    manual = []
    for x in bars:
        manual += pr.step(b, x)
        if b.closed:
            break
    assert [(e.kind, e.rule, e.price) for e in sim] == [(e.kind, e.rule, e.price) for e in manual]

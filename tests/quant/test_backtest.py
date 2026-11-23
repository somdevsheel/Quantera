"""Backtest engine on synthetic data with known answers."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from quant import backtest as bt


def calendar(n, start=date(2020, 1, 1)):
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def test_india_costs_itemised():
    c = bt.Costs()
    buy = c.trade(1_000_000, True, None, None)
    sell = c.trade(-1_000_000, False, None, None)
    assert buy["stt"] == pytest.approx(1000) and buy["stamp"] == pytest.approx(150)
    assert sell["stamp"] == 0 and buy["gst"] == pytest.approx(0.18 * (300 + 29.7 + 1.0))
    assert buy["total"] > sell["total"]
    assert c.trade(1e6, True, 1e7, 0.02)["impact"] == pytest.approx(0.1 * 0.02 * (0.1 ** 0.5) * 1e6)


def test_cap_weights_redistributes_and_keeps_cash():
    w = bt.cap_weights({"A": 8, "B": 1, "C": 1}, 0.5)
    assert w["A"] == pytest.approx(0.5) and sum(w.values()) == pytest.approx(1.0)
    w2 = bt.cap_weights({"A": 1, "B": 1}, 0.3)
    assert w2 == {"A": 0.3, "B": 0.3}  # 40% stays in cash


def test_buy_and_hold_tracks_price_and_costs_reduce_nav():
    days = calendar(300)
    px = {"A": {d: 100 * 1.001 ** i for i, d in enumerate(days)}}
    val = {"A": {d: 1e12 for d in days}}
    free = bt.Config(costs=bt.Costs(0, 0, 0, 0, 0, 0, 0, 0, 0), max_weight=1.0, top_n=1,
                     selection="TOP_N")
    out = bt.run(days, px, val, lambda d: ["A"], lambda d, names: {"A": 1.0}, free)
    first_exec = next(i for i, d in enumerate(days) if d > bt.rebalance_dates(days, "M")[0])
    navs = [e["nav"] for e in out["equity"]]
    growth = px["A"][days[-1]] / px["A"][days[first_exec]]
    assert navs[-1] / navs[first_exec] == pytest.approx(growth)
    costly = bt.run(days, px, val, lambda d: ["A"], lambda d, names: {"A": 1.0},
                    bt.Config(max_weight=1.0, top_n=1, selection="TOP_N"))
    assert costly["equity"][-1]["nav"] < navs[-1] and costly["costs"]["stt"] > 0


def test_no_lookahead_signal_sees_only_the_past():
    days = calendar(200)
    px = {n: {d: 100 + i + (j if n == "B" else 0) for i, d in enumerate(days)} for j, n in
          enumerate(["A", "B"])}
    seen = []

    def signal(d, names):
        seen.append(d)
        return {n: px[n][d] for n in names}  # uses the close of d only
    bt.run(days, px, {n: {d: 1e12 for d in days} for n in px}, lambda d: ["A", "B"], signal,
           bt.Config(top_n=1, selection="TOP_N", max_weight=1.0))
    assert seen == bt.rebalance_dates(days, "M")


def test_delisted_holding_is_sold_at_last_price():
    days = calendar(150)
    px = {"A": {d: 100.0 for d in days[:80]}, "B": {d: 50.0 for d in days}}
    out = bt.run(days, px, {n: {d: 1e12 for d in days} for n in px}, lambda d: ["A", "B"],
                 lambda d, names: {"A": 2.0, "B": 1.0}, bt.Config(top_n=1, selection="TOP_N",
                                                                 max_weight=1.0))
    assert any(e["event"] == "DELISTED_OR_STALE" and e["name"] == "A" for e in out["events"])


def test_walk_forward_picks_on_train_only_and_deflated_sharpe():
    rng = np.random.default_rng(0)
    n = 1000
    good = rng.normal(0.002, 0.01, n)  # annual Sharpe about 3: a one-year window can see it
    noise = {f"p{i}": rng.normal(0, 0.01, n) for i in range(5)}
    days = calendar(n + 1)
    wf = bt.walk_forward(days, noise | {"good": good}, train=252, test=126)
    chosen = [f["chosen"] == "good" for f in wf["folds"]]
    assert sum(chosen) / len(chosen) >= 0.8
    assert wf["out_of_sample"]["status"] == "OK"
    strong = bt.deflated_sharpe(good, trials=1)
    assert strong["deflated_sharpe_probability"] > 0.95
    lucky = bt.deflated_sharpe(noise["p0"], trials=50,
                               trial_sharpes=[x.mean() / x.std() for x in noise.values()])
    assert lucky["deflated_sharpe_probability"] < 0.95
    assert bt.deflated_sharpe(good[:10], trials=1)["status"] == "INSUFFICIENT DATA"

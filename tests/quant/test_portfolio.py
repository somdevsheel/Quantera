"""Portfolio ledger and risk: reference values and invariants."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from quant import portfolio as pf


def tx(d, kind, qty=0, px=0, fees=0, amount=0, iid="IN:A", sym="A"):
    return {"trade_date": d, "type": kind, "quantity": qty, "price": px, "fees": fees,
            "amount": amount, "instrument_id": iid, "symbol": sym, "exchange": "XNSE"}


def test_average_cost_and_realised_pnl():
    b = pf.replay([tx(date(2024, 1, 1), "DEPOSIT", amount=10000),
                   tx(date(2024, 1, 2), "BUY", 10, 100, fees=10),
                   tx(date(2024, 1, 3), "BUY", 10, 120),
                   tx(date(2024, 1, 4), "SELL", 5, 130, fees=5),
                   tx(date(2024, 1, 5), "DIVIDEND", amount=30)])
    p = b.positions["IN:A"]
    assert p.quantity == 15 and p.average_cost == pytest.approx(2210 / 20)
    assert p.realized_pnl == pytest.approx(5 * 130 - 5 - 5 * 110.5)
    assert b.cash == pytest.approx(10000 - 1010 - 1200 + 645 + 30) and p.dividends == 30


def test_oversell_rejected():
    with pytest.raises(pf.LedgerError):
        pf.replay([tx(date(2024, 1, 2), "BUY", 5, 100), tx(date(2024, 1, 3), "SELL", 6, 100)])


def test_split_applied_on_ex_date_before_trades():
    def factor(iid, after, upto):  # 1:2 split, ex-date 2024-02-01
        return 2.0 if after < date(2024, 2, 1) <= upto else 1.0
    b = pf.replay([tx(date(2024, 1, 2), "BUY", 10, 100),
                   tx(date(2024, 2, 1), "SELL", 15, 55)], factor, as_of=date(2024, 3, 1))
    p = b.positions["IN:A"]
    assert p.quantity == 5  # 10 -> 20 at the split, then 15 sold
    assert p.realized_pnl == pytest.approx(15 * 55 - 15 * 50)  # cost per share halved
    later = pf.replay([tx(date(2024, 1, 2), "BUY", 10, 100)], factor, as_of=date(2024, 3, 1))
    assert later.positions["IN:A"].quantity == 20 and later.positions["IN:A"].cost == 1000


def test_risk_report_invariants():
    rng = np.random.default_rng(0)
    m = rng.normal(0, 0.01, 300)
    r = np.column_stack([m + rng.normal(0, 0.005, 300) for _ in range(4)])
    w = np.array([0.4, 0.3, 0.2, 0.1])
    out = pf.risk_report(w, r, m, ["IT", "IT", "Bank", None], np.array([4e6, 3e6, 2e6, 1e6]),
                         [1e7, None, 1e8, 1e6], ["A", "B", "C", "D"])
    assert out["status"] == "OK"
    assert sum(out["risk_contribution"].values()) == pytest.approx(1.0)
    assert out["concentration"]["top1"] == pytest.approx(0.4)
    assert out["concentration"]["effective_names"] == pytest.approx(1 / (w ** 2).sum())
    assert out["sector_exposure"]["IT"] == pytest.approx(0.7) and "Unclassified" in out[
        "sector_exposure"]
    assert 0.8 < out["beta"] < 1.2 and out["es_95_1d_hist"] >= out["var_95_1d_hist"]
    assert out["liquidity"]["days_to_exit_at_10pct_adv"]["B"] is None
    spike = next(s for s in out["stress"] if "correlation" in s["scenario"])
    assert spike["var_95_1d"] >= out["var_95_1d_param"]  # diversification lost
    assert pf.risk_report(w, r[:30], None, ["x"] * 4, np.ones(4), [None] * 4,
                          list("ABCD"))["status"] == "INSUFFICIENT DATA"


def test_holding_states():
    assert pf.holding_state(None, None, 0.05)[0] == "INSUFFICIENT DATA"
    assert pf.holding_state("WAIT", 3, 0.05)[0] == "RISK ALERT"
    assert pf.holding_state("AVOID", 12, 0.05)[0] == "SELL REVIEW"
    assert pf.holding_state("WAIT", 50, 0.2)[0] == "TRIM REVIEW"
    assert pf.holding_state("BUY CANDIDATE", 50, 0.05)[0] == "ADD REVIEW"
    assert pf.holding_state("WAIT", 50, 0.05)[0] == "HOLD"

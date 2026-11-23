"""Market-neutral optimizer: constraints hold, labels are honest, infeasibility explained."""

from __future__ import annotations

import numpy as np
import pytest

from quant import neutral as mn


def problem(n=12, seed=0, shortable=True):
    rng = np.random.default_rng(seed)
    m = rng.normal(0, 0.01, 500)
    beta = rng.uniform(0.6, 1.4, n)
    r = np.outer(m, beta) + rng.normal(0, 0.012, (500, n))
    cov = mn.shrink_cov(r)
    mu = rng.normal(0, 0.10, n)
    sectors = ["IT" if i % 3 == 0 else "Bank" if i % 3 == 1 else "Energy" for i in range(n)]
    return mn.Problem([f"S{i}" for i in range(n)], mu, cov, beta, sectors,
                      factors={"MOM": rng.normal(0, 1, n)},
                      shortable=np.full(n, shortable))


def test_dollar_and_beta_neutral_book():
    p = problem()
    c = mn.Constraints(gross_max=1.0, max_weight=0.2)
    out = mn.solve(p, c)
    assert out["status"] in ("OPTIMAL", "APPROXIMATE")
    w = out["weights"]
    e = mn.exposures(p, w)
    assert abs(e["net"]) <= c.net_tol + 1e-6 and abs(e["beta"]) <= c.beta_tol + 1e-6
    assert e["gross"] <= 1.0 + 1e-6 and np.abs(w).max() <= 0.2 + 1e-6
    assert all(x["ok"] for x in mn.check(p, c, w))
    assert mn.label(e, c) == "DOLLAR_NEUTRAL+BETA_NEUTRAL"
    assert e["names_long"] > 0 and e["names_short"] > 0


def test_sector_factor_and_vol_target():
    p = problem(seed=1)
    c = mn.Constraints(sector_neutral=True, sector_tol=0.02, factor_neutral=("MOM",),
                       factor_tol=0.05, vol_target=0.08, gross_max=1.5, max_weight=0.25)
    w = mn.solve(p, c)["weights"]
    e = mn.exposures(p, w)
    assert all(abs(v) <= 0.02 + 1e-6 for v in e["sector_net"].values())
    assert abs(e["factor"]["MOM"]) <= 0.05 + 1e-6 and e["volatility"] <= 0.08 + 1e-5
    assert "SECTOR_NEUTRAL" in mn.label(e, c)


def test_nothing_shortable_is_infeasible_with_diagnosis():
    p = problem(shortable=False)
    out = mn.solve(p, mn.Constraints())
    assert out["status"] == "INFEASIBLE"
    d = out["diagnostics"]
    assert "dollar_neutral" in d["binding_groups"] and any("index-hedged" in h for h in d["hints"])


def test_restricted_and_liquidity_caps_respected():
    p = problem(seed=2)
    p.restricted = mn.names_index(p.names, ["S0", "S1"])
    p.liquidity_cap = np.full(len(p.names), 0.03)
    w = mn.solve(p, mn.Constraints(gross_max=0.5))["weights"]
    assert w[0] == 0 and w[1] == 0 and np.abs(w).max() <= 0.03 + 1e-6


def test_rounding_never_grows_a_leg_and_is_rechecked():
    p = problem(seed=3)
    c = mn.Constraints(gross_max=1.0, max_weight=0.2)
    w = mn.solve(p, c)["weights"]
    prices = np.full(len(w), 1234.5)
    lots = np.ones(len(w))
    shares, wr = mn.round_to_lots(w, 1_000_000, prices, lots)
    assert np.all(np.abs(wr) <= np.abs(w) + 1e-12) and np.allclose(shares, np.round(shares))
    assert len(mn.check(p, c, wr)) >= 3  # every constraint is re-evaluated on the rounded book


def test_long_only_is_never_labelled_neutral_and_stress():
    p = problem(seed=4)
    w = np.full(len(p.names), 1 / len(p.names))
    e = mn.exposures(p, w)
    assert mn.label(e, mn.Constraints()) == "LONG_ONLY (not market-neutral)"
    ws = mn.solve(p, mn.Constraints(gross_max=1.0, max_weight=0.2))["weights"]
    st = mn.stress(p, ws, np.full(len(ws), 1e8), 1e6)
    names = [s["scenario"] for s in st]
    assert any("squeeze" in n for n in names) and any("correlation" in n for n in names)
    spike = next(s for s in st if "correlation" in s["scenario"])
    assert spike["volatility"] >= 0


def test_shrinkage_is_positive_definite():
    rng = np.random.default_rng(5)
    r = rng.normal(0, 0.01, (40, 60))  # more names than observations
    c = mn.shrink_cov(r)
    assert np.linalg.eigvalsh(c).min() > 0
    assert c.shape == (60, 60) and c[0, 0] == pytest.approx(np.var(r[:, 0], ddof=1) * 252)


def test_no_signal_means_no_trade_not_an_empty_book():
    p = problem(seed=6)
    p.mu = np.zeros(len(p.names))
    out = mn.solve(p, mn.Constraints())
    assert out["status"] == "NO TRADE" and "do not cover" in out["reason"]

"""Market-neutral / low-beta portfolio construction (deterministic, documented, tested).

Problem (weights w are fractions of capital; long > 0, short < 0):

  maximise   (h/252) mu'w - lambda (h/252) w'Sw - kappa sum(c_i |w_i - w0_i|) - rho sum(w_i^2)
             over the holding horizon h trading days (default 21): annual return and risk are
             scaled to the horizon, because trading costs are paid once per rebalance
  subject to |sum w|        <= net_tol                 (dollar-neutral, if selected)
             |beta'w|       <= beta_tol                (beta-neutral, if selected)
             |sum_{i in s} w_i| <= sector_tol          (sector-neutral, per sector)
             |f'w|          <= factor_tol              (factor-neutral, per factor)
             sum |w_i|      <= gross_max
             |w_i|          <= min(max_weight, liquidity_cap_i)
             w_i >= 0 for names that cannot be shorted; w_i = 0 for restricted names
             w'Sw * 252     <= vol_target^2            (if selected)
             sum |w_i - w0_i| <= turnover_max          (if selected)

|w| is handled exactly with the split w = u - v, u, v >= 0, so every constraint except the
volatility target is linear and the objective is a convex quadratic. Solver: SciPy SLSQP.

Before optimising, a linear programme checks feasibility of the hard constraints. If they
are infeasible, an "elastic" LP adds a slack to each constraint group and minimises total
slack: the groups that need slack are reported. Hard limits are never silently relaxed.

After solving, weights are rounded to whole lots; every constraint is re-checked on the
rounded book and reported. The label states what the rounded book achieves (e.g.
DOLLAR_NEUTRAL+BETA_NEUTRAL); a book with no short side is never called market-neutral.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import linprog, minimize

F = NDArray[np.float64]
VERSION = "neutral_v1"
ANN = 252


@dataclass(frozen=True)
class Constraints:
    dollar_neutral: bool = True
    net_tol: float = 0.02
    beta_neutral: bool = True
    beta_tol: float = 0.05
    sector_neutral: bool = False
    sector_tol: float = 0.05
    factor_neutral: tuple[str, ...] = ()
    factor_tol: float = 0.10
    gross_max: float = 1.0
    max_weight: float = 0.10
    vol_target: float | None = None  # annualised
    turnover_max: float | None = None
    risk_aversion: float = 3.0
    cost_aversion: float = 1.0
    concentration: float = 0.02
    horizon_days: int = 21
    min_gross: float = 0.05  # below this the signals do not pay for the trades: NO TRADE


@dataclass
class Problem:
    names: list[str]
    mu: F  # expected annual returns (signal), used as a daily rate / 252 internally
    cov: F  # annualised covariance
    beta: F
    sectors: list[str]
    factors: dict[str, F] = field(default_factory=dict)
    shortable: NDArray[np.bool_] | None = None
    restricted: NDArray[np.bool_] | None = None
    liquidity_cap: F | None = None  # max |w_i| from liquidity
    cost_bps: F | None = None  # one-way cost per unit traded
    w0: F | None = None


def _bounds(p: Problem, c: Constraints) -> list[tuple[float, float]]:
    n = len(p.names)
    cap = np.full(n, c.max_weight)
    if p.liquidity_cap is not None:
        cap = np.minimum(cap, p.liquidity_cap)
    short = p.shortable if p.shortable is not None else np.ones(n, bool)
    blocked = p.restricted if p.restricted is not None else np.zeros(n, bool)
    ub_u = [0.0 if blocked[i] else float(cap[i]) for i in range(n)]
    ub_v = [0.0 if blocked[i] or not short[i] else float(cap[i]) for i in range(n)]
    return [(0.0, u) for u in ub_u] + [(0.0, v) for v in ub_v]


def _linear(p: Problem, c: Constraints) -> tuple[list[tuple[str, F, float]], F, F]:
    """Groups of rows  a'x <= b  over x = [u, v]; returns (rows, A, b)."""
    n = len(p.names)
    rows: list[tuple[str, F, float]] = []

    def two_sided(name: str, coef: F, tol: float) -> None:
        a = np.r_[coef, -coef]
        rows.append((name, a, tol))
        rows.append((name, -a, tol))

    if c.dollar_neutral:
        two_sided("dollar_neutral", np.ones(n), c.net_tol)
    if c.beta_neutral:
        two_sided("beta_neutral", p.beta, c.beta_tol)
    if c.sector_neutral:
        for s in sorted(set(p.sectors)):
            two_sided(f"sector:{s}", np.array([1.0 if x == s else 0.0 for x in p.sectors]),
                      c.sector_tol)
    for fname in c.factor_neutral:
        if fname in p.factors:
            two_sided(f"factor:{fname}", p.factors[fname], c.factor_tol)
    rows.append(("gross", np.ones(2 * n), c.gross_max))
    a = np.array([r[1] for r in rows]) if rows else np.zeros((0, 2 * n))
    b = np.array([r[2] for r in rows])
    return rows, a, b


def diagnose(p: Problem, c: Constraints) -> dict[str, Any]:
    """Feasibility of the linear hard constraints; with an elastic LP when infeasible."""
    n = len(p.names)
    rows, a, b = _linear(p, c)
    bounds = _bounds(p, c)
    # demand some book: at least 10% gross, else "all zero" is trivially feasible
    a_min = np.r_[[-np.ones(2 * n)]]
    lp = linprog(np.zeros(2 * n), A_ub=np.vstack([a, a_min]), b_ub=np.r_[b, -0.1],
                 bounds=bounds, method="highs")
    if lp.status == 0:
        return {"feasible": True}
    k = len(rows)
    groups = sorted({r[0] for r in rows})
    # elastic: a x - s_g <= b, minimise sum s_g
    gidx = {g: i for i, g in enumerate(groups)}
    s_cols = np.zeros((k, len(groups)))
    for i, r in enumerate(rows):
        s_cols[i, gidx[r[0]]] = -1.0
    a_el = np.vstack([np.hstack([a, s_cols]),
                      np.hstack([a_min, np.zeros((1, len(groups)))])])
    el = linprog(np.r_[np.zeros(2 * n), np.ones(len(groups))], A_ub=a_el,
                 b_ub=np.r_[b, -0.1], bounds=bounds + [(0, None)] * len(groups),
                 method="highs")
    need = ({g: float(el.x[2 * n + i]) for g, i in gidx.items() if el.x[2 * n + i] > 1e-7}
            if el.status == 0 else {})
    hints = []
    short = p.shortable if p.shortable is not None else np.ones(n, bool)
    if c.dollar_neutral and not short.any():
        hints.append("no name can be shorted: a dollar-neutral book is impossible; consider "
                     "an index-hedged or low-beta long-only book")
    return {"feasible": False, "binding_groups": need, "hints": hints}


def solve(p: Problem, c: Constraints) -> dict[str, Any]:
    n = len(p.names)
    diag = diagnose(p, c)
    if not diag["feasible"]:
        return {"status": "INFEASIBLE", "diagnostics": diag, "version": VERSION}
    _, a, b = _linear(p, c)
    bounds = _bounds(p, c)
    mu_d = p.mu * c.horizon_days / ANN  # horizon return and risk
    cov_d = p.cov * c.horizon_days / ANN
    w0 = p.w0 if p.w0 is not None else np.zeros(n)
    cost = (p.cost_bps if p.cost_bps is not None else np.full(n, 10.0)) / 1e4

    def unpack(x: F) -> F:
        return x[:n] - x[n:]

    def obj(x: F) -> float:
        w = unpack(x)
        val = (mu_d @ w - c.risk_aversion * w @ cov_d @ w
               - c.cost_aversion * cost @ np.abs(w - w0) - c.concentration * (w ** 2).sum())
        # tiny L1 on u + v removes the u, v > 0 simultaneously degeneracy
        return float(-val + 1e-6 * x.sum())

    cons: list[dict[str, Any]] = [{"type": "ineq", "fun": lambda x, a=a, b=b: b - a @ x}]
    if c.vol_target is not None:
        vt, cov_a = c.vol_target, p.cov
        cons.append({"type": "ineq",
                     "fun": lambda x: vt ** 2 - unpack(x) @ cov_a @ unpack(x)})
    if c.turnover_max is not None:
        tmax = c.turnover_max
        cons.append({"type": "ineq", "fun": lambda x: tmax - np.abs(unpack(x) - w0).sum()})
    x0 = np.array([min(0.01, hi) for _, hi in bounds])
    res = minimize(obj, x0, method="SLSQP", bounds=bounds, constraints=cons,
                   options={"maxiter": 500, "ftol": 1e-12})
    w = unpack(res.x)
    w[np.abs(w) < 1e-6] = 0.0
    if not res.success and np.abs(w).sum() == 0:
        return {"status": "INFEASIBLE", "diagnostics": {"feasible": False,
                "solver": res.message}, "version": VERSION}
    if np.abs(w).sum() < c.min_gross:
        return {"status": "NO TRADE", "weights": np.zeros(n),
                "reason": "expected returns over the horizon do not cover risk and trading "
                          "costs; no book is proposed", "version": VERSION}
    return {"status": "OPTIMAL" if res.success else "APPROXIMATE", "weights": w,
            "solver": {"name": "scipy SLSQP", "iterations": int(res.nit),
                       "message": str(res.message)}, "version": VERSION}


def exposures(p: Problem, w: F) -> dict[str, Any]:
    long_, short_ = float(w[w > 0].sum()), float(-w[w < 0].sum())
    sec: dict[str, float] = {}
    for s, x in zip(p.sectors, w, strict=True):
        sec[s] = sec.get(s, 0.0) + float(x)
    return {"long": long_, "short": short_, "gross": long_ + short_, "net": long_ - short_,
            "beta": float(p.beta @ w), "volatility": math.sqrt(max(float(w @ p.cov @ w), 0.0)),
            "expected_return": float(p.mu @ w), "names_long": int((w > 0).sum()),
            "names_short": int((w < 0).sum()), "sector_net": sec,
            "factor": {k: float(v @ w) for k, v in p.factors.items()}}


def check(p: Problem, c: Constraints, w: F) -> list[dict[str, Any]]:
    e = exposures(p, w)
    out = [{"name": "gross", "value": e["gross"], "limit": f"<= {c.gross_max}",
            "ok": e["gross"] <= c.gross_max + 1e-6},
           {"name": "max_weight", "value": float(np.abs(w).max()) if len(w) else 0.0,
            "limit": f"<= {c.max_weight}",
            "ok": float(np.abs(w).max() if len(w) else 0.0) <= c.max_weight + 1e-6}]
    if c.dollar_neutral:
        out.append({"name": "dollar_neutral", "value": e["net"], "limit": f"|net| <= {c.net_tol}",
                    "ok": abs(e["net"]) <= c.net_tol + 1e-6})
    if c.beta_neutral:
        out.append({"name": "beta_neutral", "value": e["beta"],
                    "limit": f"|beta| <= {c.beta_tol}", "ok": abs(e["beta"]) <= c.beta_tol + 1e-6})
    if c.sector_neutral:
        worst = max((abs(v) for v in e["sector_net"].values()), default=0.0)
        out.append({"name": "sector_neutral", "value": worst,
                    "limit": f"|sector net| <= {c.sector_tol}", "ok": worst <= c.sector_tol + 1e-6})
    if c.vol_target is not None:
        out.append({"name": "vol_target", "value": e["volatility"],
                    "limit": f"<= {c.vol_target}", "ok": e["volatility"] <= c.vol_target + 1e-6})
    return out


def label(e: dict[str, Any], c: Constraints) -> str:
    if e["short"] == 0:
        return "LONG_ONLY (not market-neutral)"
    parts = []
    if abs(e["net"]) <= c.net_tol + 1e-6:
        parts.append("DOLLAR_NEUTRAL")
    if abs(e["beta"]) <= c.beta_tol + 1e-6:
        parts.append("BETA_NEUTRAL")
    if c.sector_neutral and all(abs(v) <= c.sector_tol + 1e-6 for v in e["sector_net"].values()):
        parts.append("SECTOR_NEUTRAL")
    return "+".join(parts) if parts else "LONG_SHORT (not neutral)"


def round_to_lots(w: F, capital: float, prices: F, lots: F) -> tuple[F, F]:
    """(shares, rounded weights): nearest whole lot towards zero, so no leg grows."""
    raw = w * capital / prices
    shares = np.trunc(raw / lots) * lots
    return shares, shares * prices / capital


def stress(p: Problem, w: F, adv_value: F | None, capital: float) -> list[dict[str, Any]]:
    e = exposures(p, w)
    out = [{"scenario": "market gap -10%", "pnl_pct": -0.10 * e["beta"],
            "note": "beta-implied; ignores stock-specific moves"}]
    sd = np.sqrt(np.clip(np.diag(p.cov), 0, None))
    spike = np.outer(sd, sd) * 0.9
    np.fill_diagonal(spike, sd ** 2)
    out.append({"scenario": "correlation spike (rho 0.9)",
                "volatility": math.sqrt(max(float(w @ spike @ w), 0.0)),
                "note": "neutral books lose their diversification when correlations rise"})
    out.append({"scenario": "volatility x2", "volatility": 2 * e["volatility"]})
    if e["sector_net"]:
        s, v = max(e["sector_net"].items(), key=lambda kv: abs(kv[1]))
        out.append({"scenario": f"{s} sector -15%", "pnl_pct": -0.15 * v})
    if (w < 0).any():
        i = int(np.argmin(w))
        out.append({"scenario": f"short squeeze: {p.names[i]} +30%",
                    "pnl_pct": 0.30 * float(w[i]), "note": "largest short rallies"})
    if adv_value is not None:
        days = np.where(adv_value > 0, np.abs(w) * capital / (0.10 * adv_value * 0.5), np.inf)
        out.append({"scenario": "liquidity halves", "max_days_to_exit": float(days.max())
                    if len(days) else 0.0, "note": "10% of a halved 20-day traded value"})
    return out


def shrink_cov(returns: F) -> F:
    """Ledoit-Wolf style shrinkage to the constant-correlation-free diagonal target
    (annualised). Stabilises the sample covariance of many names on few observations."""
    r = np.asarray(returns, dtype=float)
    t = r.shape[0]
    s = np.cov(r, rowvar=False, ddof=1)
    target = np.diag(np.diag(s))
    x = r - r.mean(axis=0)
    phi = sum(float(((np.outer(row, row) - s) ** 2).sum()) for row in x) / t
    gamma = float(((s - target) ** 2).sum())
    delta = max(0.0, min(1.0, phi / gamma / t)) if gamma > 0 else 1.0
    return ((1 - delta) * s + delta * target) * ANN


def names_index(names: Sequence[str], pick: Sequence[str]) -> NDArray[np.bool_]:
    s = set(pick)
    return np.array([n in s for n in names])

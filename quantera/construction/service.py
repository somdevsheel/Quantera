"""Portfolio construction jobs: universe snapshot + preferences -> deterministic optimizer ->
explained proposal (legs, exposures, constraint checks after rounding, stress, stop risk).

Flow (PRD 13): propose -> explain -> simulate -> explicit user approval for PAPER only.

Data and defaults (stated in every result):
* Covariance: shrunk sample covariance of 1 year of total-return daily returns.
* Beta: OLS against NIFTY 50 over the same window.
* Signal: caller-supplied expected returns, or MOMENTUM_12_1 cross-sectional z-score x 5% a
  year (a documented heuristic; MODEL_OUTPUT, not a forecast).
* Shorting is OFF unless the caller lists shortable symbols. In India a cash
  short needs SLB or F&O, and no borrow-availability feed is connected. LONG_SHORT with
  nothing shortable is INFEASIBLE with a hint to use INDEX_HEDGED or LOW_BETA_LONG_ONLY.
* INDEX_HEDGED: a long book plus a NIFTY 50 hedge sized to its beta; labelled as index-hedged,
  never as a market-neutral long/short book.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from quant import features as fx
from quant import neutral as mn
from quantera.analysis.jobs import Cancelled, Progress
from quantera.analysis.models import AnalysisJob
from quantera.analysis.scan import BENCHMARK
from quantera.lake.client import LakeClient
from quantera.universes.models import UniverseSnapshot

LOOKBACK_DAYS = 400
MOMENTUM_SCALE = 0.05


class ConstructionIn(BaseModel):
    name: str = Field(default="Proposal", min_length=1, max_length=120)
    universe_snapshot_id: uuid.UUID | None = None
    universe_id: uuid.UUID | None = None
    as_of: date | None = None
    mode: Literal["LONG_SHORT", "INDEX_HEDGED", "LOW_BETA_LONG_ONLY"] = "LONG_SHORT"
    capital: float = Field(default=1_000_000, gt=0)
    signal: Literal["momentum_12_1", "provided"] = "momentum_12_1"
    expected_returns: dict[str, float] = Field(default_factory=dict)
    shortable: list[str] = Field(default_factory=list, max_length=3000)
    restricted: list[str] = Field(default_factory=list, max_length=3000)
    exited: list[str] = Field(default_factory=list, max_length=3000)  # legs that exited
    dollar_neutral: bool = True
    net_tol: float = Field(default=0.02, ge=0, le=1)
    beta_neutral: bool = True
    beta_tol: float = Field(default=0.05, ge=0, le=2)
    sector_neutral: bool = False
    sector_tol: float = Field(default=0.05, ge=0, le=1)
    factor_neutral: list[Literal["MOMENTUM"]] = Field(default_factory=list)
    gross_max: float = Field(default=1.0, gt=0, le=4)
    max_weight: float = Field(default=0.10, gt=0, le=1)
    vol_target: float | None = Field(default=None, gt=0, le=1)
    adv_participation: float = Field(default=0.10, gt=0, le=1)
    liquidity_days: float = Field(default=1.0, gt=0, le=20)
    cost_bps: float = Field(default=10.0, ge=0, le=200)
    stop_vol_multiple: float = Field(default=2.0, gt=0, le=10)
    horizon_days: int = Field(default=21, ge=5, le=252)


def _close(rows: list[dict[str, Any]]) -> dict[date, float]:
    return {date.fromisoformat(str(r["trade_date"])[:10]): float(r["close"]) for r in rows
            if r.get("close")}


def run(sessions: Callable[[], Session], lake: LakeClient, job_id: uuid.UUID) -> None:
    from quantera.portfolios.service import sector_map

    with sessions() as s:
        job = s.get(AnalysisJob, job_id)
        assert job is not None and job.as_of and job.universe_snapshot_id
        snap = s.get(UniverseSnapshot, job.universe_snapshot_id)
        assert snap is not None
        cfg = ConstructionIn(**job.params["request"])
        members, as_of, release = list(snap.members), job.as_of, snap.lake_release
    prog = Progress(sessions, job_id)
    start = as_of - timedelta(days=LOOKBACK_DAYS)
    bench = _close(lake.index_daily(BENCHMARK, start, as_of))
    series, raw_close, adv, mom, keep = [], [], [], [], []
    notes: list[str] = []
    for i, m in enumerate(members, 1):
        try:
            _, tr = lake.prices_daily(instrument_id=m["instrument_id"], exchange=m.get("exchange"),
                                      start=start, end=as_of, adjust="total", as_of=as_of)
            b = fx.Bars.from_rows(tr)
            f = fx.price_features(b, bench)
            if len(b) < 200:
                notes.append(f"{m['symbol']}: under 200 days of prices, excluded")
                continue
            _, raw = lake.prices_daily(instrument_id=m["instrument_id"], exchange=m.get("exchange"),
                                       start=as_of - timedelta(days=15), end=as_of, adjust="none",
                                       as_of=as_of)
            series.append(dict(zip(b.dates, b.close, strict=True)))
            raw_close.append(float(raw[-1]["close"]))
            adv.append(f.get("ADV_VALUE_20D") or 0.0)
            mom.append(f.get("MOMENTUM_12_1"))
            keep.append(m)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{m['symbol']}: {type(exc).__name__}, excluded")
        if (i % 10 == 0 or i == len(members)) and not prog.update(i, len(members)):
            raise Cancelled
    result = construct(cfg, keep, series, raw_close, adv, mom, bench, sector_map(lake), notes)
    result |= {"as_of": as_of.isoformat(), "lake_release": release,
               "universe_snapshot_id": str(snap.id), "universe_hash": snap.content_hash}
    with sessions() as s:
        j = s.get(AnalysisJob, job_id)
        assert j is not None
        j.summary = {"proposal": result}
        j.versions = {"optimizer": mn.VERSION, "lake_release": release}
        j.status = "SUCCEEDED"
        s.commit()


def construct(cfg: ConstructionIn, members: list[dict[str, Any]], series: list[dict[date, float]],
              raw_close: list[float], adv: list[float], mom: list[float | None],
              bench: dict[date, float], sectors_of: dict[str, str],
              notes: list[str]) -> dict[str, Any]:
    syms = [m["symbol"] for m in members]
    if len(syms) < 2:
        return {"status": "INSUFFICIENT DATA", "notes": notes}
    common = sorted(set(bench).intersection(*[set(x) for x in series]))[-253:]
    px = np.array([[x[d] for x in series] for d in common])
    rets = px[1:] / px[:-1] - 1
    bp = np.array([bench[d] for d in common])
    br = bp[1:] / bp[:-1] - 1
    vb = br.var(ddof=1)
    beta = np.array([np.cov(rets[:, i], br, ddof=1)[0, 1] / vb for i in range(len(syms))])
    cov = mn.shrink_cov(rets)
    if cfg.signal == "provided":
        mu = np.array([cfg.expected_returns.get(s, 0.0) for s in syms])
    else:
        m = np.array([np.nan if v is None else v for v in mom])
        ok = np.isfinite(m)
        mu = np.zeros(len(syms))
        if ok.sum() >= 2 and m[ok].std() > 0:
            mu[ok] = MOMENTUM_SCALE * (m[ok] - m[ok].mean()) / m[ok].std()
    zmom = mu / MOMENTUM_SCALE if cfg.signal != "provided" else mu
    sectors = [sectors_of.get(mm["instrument_id"], "Unclassified") for mm in members]
    upper = {s.upper() for s in cfg.shortable}
    long_only = cfg.mode in ("INDEX_HEDGED", "LOW_BETA_LONG_ONLY")
    shortable = np.array([(s in upper) and not long_only for s in syms])
    restricted = mn.names_index(syms, [s.upper() for s in cfg.restricted + cfg.exited])
    liq = np.array([cfg.adv_participation * a * cfg.liquidity_days / cfg.capital if a else 0.0
                    for a in adv])
    p = mn.Problem(syms, mu, cov, beta, sectors, factors={"MOMENTUM": zmom},
                   shortable=shortable, restricted=restricted, liquidity_cap=liq,
                   cost_bps=np.full(len(syms), cfg.cost_bps))
    c = mn.Constraints(
        dollar_neutral=cfg.dollar_neutral and cfg.mode == "LONG_SHORT", net_tol=cfg.net_tol,
        beta_neutral=cfg.beta_neutral and cfg.mode == "LONG_SHORT", beta_tol=cfg.beta_tol,
        sector_neutral=cfg.sector_neutral and cfg.mode == "LONG_SHORT", sector_tol=cfg.sector_tol,
        factor_neutral=tuple(cfg.factor_neutral), gross_max=cfg.gross_max,
        max_weight=cfg.max_weight, vol_target=cfg.vol_target, horizon_days=cfg.horizon_days)
    out: dict[str, Any] = {"version": mn.VERSION, "mode": cfg.mode, "capital": cfg.capital,
                           "estimation": {"observations": len(rets),
                                          "window_start": common[0].isoformat(),
                                          "window_end": common[-1].isoformat(),
                                          "covariance": "shrunk sample covariance",
                                          "benchmark": BENCHMARK},
                           "signal": {"kind": cfg.signal, "classification": "MODEL_OUTPUT",
                                      "note": "momentum heuristic, not a forecast"
                                      if cfg.signal != "provided" else "caller-provided"},
                           "constraints": vars(c), "notes": notes}
    if cfg.mode == "LONG_SHORT" and not shortable.any():
        notes.append("no shortable names were given: in Indian cash equities a short needs "
                     "SLB/F&O, so a long/short book is not proposed")
    # Solve with half the neutrality tolerances, so rounding to whole shares stays inside the
    # user's limits; every check below uses the user's full tolerances.
    solve_c = mn.Constraints(**(vars(c) | {"net_tol": c.net_tol / 2, "beta_tol": c.beta_tol / 2,
                                           "sector_tol": c.sector_tol / 2,
                                           "factor_tol": c.factor_tol / 2}))
    res = mn.solve(p, solve_c)
    out["status"] = res["status"]
    if res["status"] in ("INFEASIBLE", "NO TRADE"):
        out["diagnostics"] = res.get("diagnostics") or {"reason": res.get("reason")}
        if res["status"] == "INFEASIBLE" and cfg.mode == "LONG_SHORT":
            out["alternatives"] = ["INDEX_HEDGED", "LOW_BETA_LONG_ONLY"]
        return out
    w = res["weights"]
    shares, wr = mn.round_to_lots(w, cfg.capital, np.array(raw_close), np.ones(len(w)))
    hedge = None
    if cfg.mode == "INDEX_HEDGED":
        hb = float(beta @ wr)
        hedge = {"instrument": BENCHMARK, "weight": -hb,
                 "note": "short NIFTY 50 exposure (e.g. index futures) sized to the long "
                         "book's beta; execution and margin are not modelled"}
    e = mn.exposures(p, wr)
    if hedge:
        e["net_after_hedge"] = e["net"] + hedge["weight"]
        e["beta_after_hedge"] = e["beta"] + hedge["weight"]
    checks = mn.check(p, c, wr)
    lab = (f"INDEX_HEDGED (beta {e['beta_after_hedge']:+.2f}; not a long/short neutral book)"
           if hedge else mn.label(e, c))
    daily_vol = np.sqrt(np.clip(np.diag(cov), 0, None) / 252)
    legs = []
    for i, s in enumerate(syms):
        if shares[i] == 0:
            continue
        stop_dist = cfg.stop_vol_multiple * daily_vol[i]
        legs.append({"symbol": s, "instrument_id": members[i]["instrument_id"],
                     "exchange": members[i].get("exchange"),
                     "side": "LONG" if wr[i] > 0 else "SHORT", "weight": float(wr[i]),
                     "shares": float(shares[i]), "price": raw_close[i],
                     "notional": float(shares[i] * raw_close[i]), "beta": float(beta[i]),
                     "sector": sectors[i], "expected_return": float(mu[i]),
                     "stop_distance_pct": float(stop_dist),
                     "loss_at_stop": float(abs(wr[i]) * stop_dist * cfg.capital)})
    rc = wr * (cov @ wr) / max(float(wr @ cov @ wr), 1e-18)
    for leg in legs:
        leg["risk_contribution"] = float(rc[syms.index(leg["symbol"])])
    out |= {"label": lab, "weights_rounded": True, "exposures": e, "checks": checks,
            "all_checks_pass": all(x["ok"] for x in checks), "legs": legs, "hedge": hedge,
            "stress": mn.stress(p, wr, np.array(adv), cfg.capital),
            "stop_risk": {"method": f"VOLATILITY stop, {cfg.stop_vol_multiple} x daily vol",
                          "all_stops_hit": float(sum(x["loss_at_stop"] for x in legs)),
                          "note": "planned loss at the stops; gaps can make it larger"},
            "solver": res.get("solver"),
            "disclaimer": "Proposal only: not advice, not an order. Correlations and betas "
                          "change; a neutral book can still lose money (stock-specific, tail, "
                          "liquidity, financing and model risk). Live trading is disabled."}
    if cfg.exited:
        out["rebalanced_after_exit"] = cfg.exited
    if not math.isfinite(e["volatility"]):
        out["status"] = "INFEASIBLE"
    return out

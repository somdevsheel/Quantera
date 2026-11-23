"""Cross-sectional ranking and decision states (pure, deterministic, versioned policy).

Pillars are 0-100 percentiles within the scanned universe (higher = better):

  MOMENTUM   MOMENTUM_12_1, RETURN_6M, PCT_OF_52W_HIGH, DIST_SMA200     (price evidence)
  QUALITY    NET_MARGIN_TTM                                              (fundamental)
  GROWTH     REVENUE_GROWTH_YOY, EPS_GROWTH_YOY                          (fundamental)
  VALUATION  EARNINGS_YIELD_TTM (only when positive)                     (fundamental)
  RISK       VOLATILITY_1Y, DOWNSIDE_VOL_1Y, MAX_DRAWDOWN_1Y, |BETA - 1|  (lower risk = higher)

Correlated inputs are averaged INSIDE their pillar, so one idea is counted once (RISK too: the
worst of four noisy readings flagged ~40% of NIFTY 50 as risky by chance; the average flags
genuinely extreme stocks). A percentile needs
MIN_PEERS non-missing peers; an input with no variation is dropped (it cannot discriminate).
A pillar without usable inputs is None: missing data is never a neutral 50.

States
  Candidate: BUY CANDIDATE, WAIT, AVOID, NO TRADE, INSUFFICIENT DATA, STALE DATA
  Overrides only downgrade: BLOCKED BY RISK (-> AVOID), illiquid (-> NO TRADE),
  no fresh independent fundamental evidence (BUY CANDIDATE -> WAIT).
These are research classifications, not advice or orders.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import numpy as np

POLICY_VERSION = "decision_policy_v1"
MIN_PEERS = 5

PILLARS: dict[str, list[tuple[str, int]]] = {  # feature, direction (+1 higher is better)
    "MOMENTUM": [("MOMENTUM_12_1", 1), ("RETURN_6M", 1), ("PCT_OF_52W_HIGH", 1),
                 ("DIST_SMA200", 1)],
    "QUALITY": [("NET_MARGIN_TTM", 1)],
    "GROWTH": [("REVENUE_GROWTH_YOY", 1), ("EPS_GROWTH_YOY", 1)],
    "VALUATION": [("EARNINGS_YIELD_TTM", 1)],
    "RISK": [("VOLATILITY_1Y", -1), ("DOWNSIDE_VOL_1Y", -1), ("MAX_DRAWDOWN_1Y", 1),
             ("BETA_DISTANCE", -1)],
}
FUNDAMENTAL_PILLARS = ("QUALITY", "GROWTH", "VALUATION")
OPPORTUNITY_WEIGHTS = {"MOMENTUM": 0.35, "QUALITY": 0.20, "GROWTH": 0.20, "VALUATION": 0.25}
STATE_ORDER = ["BUY CANDIDATE", "WAIT", "NO TRADE", "AVOID", "STALE DATA", "INSUFFICIENT DATA"]


@dataclass(frozen=True)
class Policy:
    version: str = POLICY_VERSION
    buy_opportunity: float = 70.0
    wait_opportunity: float = 50.0
    max_uncertainty_for_buy: float = 45.0
    min_risk_score: float = 15.0  # risk pillar below this -> AVOID (BLOCKED BY RISK)
    min_adv_value: float = 5e7  # INR traded value per day (median of 20 days)
    max_price_age_days: int = 7  # calendar days between the last bar and the cutoff
    max_fundamental_age_days: int = 200  # period end older than this -> fundamentals stale
    min_history_days: int = 253


@dataclass
class Decision:
    instrument_id: str
    symbol: str
    state: str
    coverage: str  # COMPLETE | PARTIAL | STALE | UNSUPPORTED | FAILED
    pillars: dict[str, float | None]
    opportunity: float | None
    uncertainty: float
    supporting: list[str] = field(default_factory=list)
    counter: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    overrides: list[str] = field(default_factory=list)
    explanation: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def percentiles(values: Sequence[float | None], direction: int) -> list[float | None]:
    """0-100 percentile rank among non-missing values; ties share their average rank."""
    arr = np.array([np.nan if v is None else float(v) for v in values])
    ok = np.isfinite(arr)
    out: list[float | None] = [None] * len(values)
    if ok.sum() < MIN_PEERS or np.ptp(arr[ok]) == 0:
        return out
    x = direction * arr[ok]
    order = x.argsort(kind="stable")
    ranks = np.empty(len(x))
    ranks[order] = np.arange(len(x))
    for v in np.unique(x):
        m = x == v
        ranks[m] = ranks[m].mean()
    pct = 100.0 * ranks / max(len(x) - 1, 1)
    for k, i in enumerate(np.flatnonzero(ok)):
        out[i] = float(pct[k])
    return out


def _input(code: str, f: Mapping[str, Any]) -> float | None:
    if code == "BETA_DISTANCE":
        b = f.get("BETA_1Y")
        return abs(b - 1.0) if b is not None else None
    v = f.get(code)
    if code == "EARNINGS_YIELD_TTM" and v is not None and v <= 0:
        return None  # a loss-maker is not "cheap"; it is reported as counter-evidence instead
    return None if v is None else float(v)


def score(rows: Sequence[Mapping[str, Any]], *, as_of: date,
          policy: Policy = Policy()) -> list[Decision]:
    """``rows``: {instrument_id, symbol, features: {...}, price_as_of: date|None,
    fundamentals_period_end: date|None, failed: str|None}."""
    codes = [c for items in PILLARS.values() for c, _ in items]
    cols = {c: [_input(c, r.get("features") or {}) for r in rows] for c in codes}
    dirs = {c: d for items in PILLARS.values() for c, d in items}
    pct = {c: percentiles(cols[c], dirs[c]) for c in codes}
    out: list[Decision] = []
    for i, r in enumerate(rows):
        f = r.get("features") or {}
        sym, iid = str(r.get("symbol")), str(r.get("instrument_id"))
        if r.get("failed"):
            out.append(Decision(iid, sym, "INSUFFICIENT DATA", "FAILED", dict.fromkeys(PILLARS),
                                None, 100.0, missing=["all"],
                                explanation=f"data could not be loaded: {r['failed']}"))
            continue
        price_as_of: date | None = r.get("price_as_of")
        fund_end: date | None = r.get("fundamentals_period_end")
        price_stale = (price_as_of is None
                       or (as_of - price_as_of).days > policy.max_price_age_days)
        fund_fresh = (fund_end is not None
                      and (as_of - fund_end).days <= policy.max_fundamental_age_days)
        pillars: dict[str, float | None] = {}
        missing: list[str] = []
        for p, items in PILLARS.items():
            if p in FUNDAMENTAL_PILLARS and not fund_fresh:
                pillars[p] = None
                missing += [c for c, _ in items]
                continue
            vals = [pct[c][i] for c, _ in items]
            have = [v for v in vals if v is not None]
            missing += [c for (c, _), v in zip(items, vals, strict=True) if v is None]
            if not have:
                pillars[p] = None
            else:
                pillars[p] = float(np.mean(have))
        avail = {p: v for p in OPPORTUNITY_WEIGHTS if (v := pillars[p]) is not None}
        opp = (sum(OPPORTUNITY_WEIGHTS[p] * v for p, v in avail.items())
               / sum(OPPORTUNITY_WEIGHTS[p] for p in avail)) if avail else None
        fund_count = sum(1 for p in FUNDAMENTAL_PILLARS if pillars[p] is not None)
        coverage_share = sum(v is not None for v in pillars.values()) / len(pillars)
        history_short = (f.get("HISTORY_DAYS") or 0) < policy.min_history_days
        uncertainty = min(100.0, 60.0 * (1 - coverage_share) + (20.0 if price_stale else 0.0)
                          + (15.0 if history_short else 0.0)
                          + 25.0 * float(f.get("ZERO_VOLUME_SHARE_3M") or 0.0))
        coverage = ("STALE" if price_stale else "COMPLETE" if coverage_share == 1 else
                    "UNSUPPORTED" if coverage_share == 0 else "PARTIAL")
        d = Decision(iid, sym, "", coverage, pillars, opp, round(uncertainty, 1), missing=missing)
        for p, v in pillars.items():
            if v is None:
                continue
            if v >= 70:
                d.supporting.append(f"{p.lower()} {v:.0f}th percentile")
            elif v <= 30:
                d.counter.append(f"{p.lower()} {v:.0f}th percentile")
        ey = f.get("EARNINGS_YIELD_TTM")
        if ey is not None and ey <= 0 and fund_fresh:
            d.counter.append("loss-making over the last four quarters")
        if fund_end is None:
            d.counter.append("no published results available")
        elif not fund_fresh:
            d.counter.append(f"latest results are for the period ending {fund_end} "
                             f"(older than {policy.max_fundamental_age_days} days): not used")
        # ---- state
        adv = f.get("ADV_VALUE_20D")
        if price_stale:
            d.state = "STALE DATA"
        elif pillars["MOMENTUM"] is None or history_short:
            d.state = "INSUFFICIENT DATA"
        elif opp is not None and opp >= policy.buy_opportunity and \
                uncertainty <= policy.max_uncertainty_for_buy:
            d.state = "BUY CANDIDATE"
        elif opp is not None and opp >= policy.wait_opportunity:
            d.state = "WAIT"
        else:
            d.state = "NO TRADE"
        if d.state in ("BUY CANDIDATE", "WAIT", "NO TRADE"):
            if d.state == "BUY CANDIDATE" and fund_count == 0:
                d.state = "WAIT"
                d.overrides.append("no fresh fundamental evidence: price evidence alone is not "
                                   "enough for a BUY CANDIDATE")
            risk = pillars["RISK"]
            if risk is not None and risk < policy.min_risk_score:
                d.state = "AVOID"
                d.overrides.append(f"BLOCKED BY RISK: risk percentile {risk:.0f} < "
                                   f"{policy.min_risk_score:.0f}")
            if adv is None or adv < policy.min_adv_value:
                d.overrides.append("liquidity unknown" if adv is None else
                                   f"liquidity {adv / 1e7:.1f} Cr/day below "
                                   f"{policy.min_adv_value / 1e7:.1f} Cr/day")
                if d.state in ("BUY CANDIDATE", "WAIT"):
                    d.state = "NO TRADE"
        o = "n/a" if opp is None else f"{opp:.0f}"
        d.explanation = (f"{d.state}: opportunity {o}, uncertainty {uncertainty:.0f}, "
                         f"{fund_count} fresh fundamental pillar(s)"
                         + (f"; overrides: {'; '.join(d.overrides)}" if d.overrides else ""))
        out.append(d)
    out.sort(key=lambda x: (STATE_ORDER.index(x.state), -(x.opportunity or -1), x.symbol))
    return out

"""Stock research workspace: one canonical page per instrument (/stocks/{exchange}/{symbol}).

Every section carries its own status (OK | PARTIAL | STALE | UNSUPPORTED | MISSING), data
date and source, so a missing or stale input is shown as such, never as a plausible number.
All calculations are the tested functions in ``quant``; nothing is computed in the clients.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from quant import features as fx
from quant import indicators as ind
from quant import stats
from quant import volatility as vol
from quant.scoring import Policy
from quantera.analysis.models import AnalysisJob, DecisionRecord
from quantera.analysis.scan import BENCHMARK, latest_market_date
from quantera.core.errors import DomainError, NotFound
from quantera.identity.service import Principal
from quantera.lake.client import LakeClient

EXCHANGES = {"NSE": "XNSE", "XNSE": "XNSE", "BSE": "XBOM", "XBOM": "XBOM"}
CHART_DAYS = 400  # calendar days of bars for the chart (about 1.5 years of sessions)
HISTORY_DAYS = 1900  # for 200-day averages, 1-year statistics and 52-week ranges


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _series(a: np.ndarray, n: int) -> list[float | None]:
    return [_f(v) for v in a[-n:]]


def resolve(lake: LakeClient, exchange: str, symbol: str) -> dict[str, Any]:
    mic = EXCHANGES.get(exchange.upper())
    if mic is None:
        raise DomainError("exchange must be NSE or BSE")
    hits = [h for h in lake.resolve(symbol.upper(), mic) if h.get("exchange_mic") == mic]
    if not hits:
        raise NotFound(f"{symbol.upper()} is not listed on {mic} in the lake")
    if len(hits) > 1:
        raise DomainError(f"{symbol.upper()} matches several listings on {mic}",
                          {"candidates": [h.get("instrument_id") for h in hits]})
    return hits[0]


def research(session: Session, p: Principal, lake: LakeClient, exchange: str, symbol: str,
             as_of: date | None = None) -> dict[str, Any]:
    inst = resolve(lake, exchange, symbol)
    iid, mic, sym = inst["instrument_id"], inst["exchange_mic"], inst["symbol"]
    bse = inst.get("code") if mic == "XBOM" else None  # exact BSE scrip (see LakeClient)
    as_of = as_of or latest_market_date(lake, date.today())
    start = as_of - timedelta(days=HISTORY_DAYS)
    meta_t, tr = lake.prices_daily(instrument_id=iid, exchange=mic, start=start, end=as_of,
                                   adjust="total", as_of=as_of, bse_code=bse)
    _, sp = lake.prices_daily(instrument_id=iid, exchange=mic, start=start, end=as_of,
                              adjust="split", as_of=as_of, bse_code=bse)
    release = meta_t.get("release")
    if not sp:
        raise NotFound(f"No prices for {sym} on or before {as_of}")
    policy = Policy()
    dates = [str(r["trade_date"])[:10] for r in sp]
    o, h, lo, c = (np.array([float(r.get(k) or r["close"]) for r in sp])
                   for k in ("open", "high", "low", "close"))
    volume = np.array([float(r.get("volume") or 0) for r in sp])
    last_date = date.fromisoformat(dates[-1])
    stale = (as_of - last_date).days > policy.max_price_age_days
    bench_rows = lake.index_daily(BENCHMARK, start, as_of)
    bench = {date.fromisoformat(str(r["trade_date"])[:10]): float(r["close"]) for r in bench_rows}
    tbars = fx.Bars.from_rows(tr)
    pf = fx.price_features(tbars, bench)
    out: dict[str, Any] = {
        "instrument": {"instrument_id": iid, "symbol": sym, "exchange": mic,
                       "name": inst.get("name"), "route": f"/stocks/{mic}/{sym}"},
        "as_of": as_of.isoformat(), "lake_release": release,
        "disclaimer": "Research information, not investment advice. Figures are from the "
                      "stated data dates; stale or missing inputs are marked, never filled in."}
    w52 = slice(-252, None)
    prev = c[-2] if len(c) > 1 else None
    out["overview"] = {
        "status": "STALE" if stale else "OK", "last_date": dates[-1], "close": _f(c[-1]),
        "change": _f(c[-1] - prev) if prev else None,
        "change_pct": _f(c[-1] / prev - 1) if prev else None,
        "volume": _f(volume[-1]), "traded_value": _f(sp[-1].get("traded_value")),
        "high_52w": _f(h[w52].max()), "low_52w": _f(lo[w52].min()),
        "adv_value_20d": pf.get("ADV_VALUE_20D"),
        "freshness": {"prices_last": dates[-1], "stale": stale,
                      "max_age_days": policy.max_price_age_days},
        "decision": _latest_decision(session, p, iid)}
    # ----- technical (split-adjusted bars: price levels comparable over time)
    n = min(len(c), 260)
    m_line, m_sig, m_hist = ind.macd(c)
    b_lo, b_mid, b_hi = ind.bollinger(c)
    adx_v, pdi, mdi = ind.adx(h, lo, c)
    k, d = ind.stochastic(h, lo, c)
    sma50, sma200 = ind.sma(c, 50), ind.sma(c, 200)
    sup, res = ind.support_resistance(h, lo, 20)
    rsi = ind.rsi(c)
    out["technical"] = {
        "status": "OK" if len(c) >= 200 else "PARTIAL", "adjustment": "split",
        "bars": [{"date": dates[i], "open": _f(o[i]), "high": _f(h[i]), "low": _f(lo[i]),
                  "close": _f(c[i]), "volume": _f(volume[i])} for i in range(len(c) - n, len(c))],
        "indicators": {"sma20": _series(ind.sma(c, 20), n), "sma50": _series(sma50, n),
                       "sma200": _series(sma200, n), "ema20": _series(ind.ema(c, 20), n),
                       "rsi14": _series(rsi, n), "macd": _series(m_line, n),
                       "macd_signal": _series(m_sig, n), "macd_hist": _series(m_hist, n),
                       "bb_lower": _series(b_lo, n), "bb_mid": _series(b_mid, n),
                       "bb_upper": _series(b_hi, n), "atr14": _series(ind.atr(h, lo, c), n),
                       "adx14": _series(adx_v, n), "plus_di": _series(pdi, n),
                       "minus_di": _series(mdi, n), "stoch_k": _series(k, n),
                       "stoch_d": _series(d, n)},
        "signals": {"above_sma50": _cmp(c[-1], sma50[-1]), "above_sma200": _cmp(c[-1], sma200[-1]),
                    "golden_cross": _cmp(sma50[-1], sma200[-1]), "rsi14": _f(rsi[-1]),
                    "adx14": _f(adx_v[-1]), "trend_strength": _trend(_f(adx_v[-1])),
                    "support_20d": sup, "resistance_20d": res,
                    "breakout_52w": bool(c[-1] >= h[w52][:-1].max()) if len(h) > 2 else None,
                    "pct_of_52w_high": pf.get("PCT_OF_52W_HIGH")},
        "notes": ["VWAP and intraday intervals need intraday data, which the lake does not hold"]}
    # ----- volatility
    ests = {f"{lab}": vol.all_estimators(o, h, lo, c, window=wdw)
            for lab, wdw in (("1m", 21), ("3m", 63), ("1y", 252)) if len(c) > wdw + 1}
    out["volatility"] = {"status": "OK" if "1y" in ests else "PARTIAL", "estimators": ests,
                         "rolling_21d": _series(stats.rolling_volatility(tbars.close, 21), n - 1),
                         "notes": ["annualised with 252 days; Parkinson and Garman-Klass ignore "
                                   "overnight gaps, Yang-Zhang includes them"]}
    # ----- quantitative statistics (total-return prices vs NIFTY 50, last year)
    tclose = tbars.close[-253:]
    tdates = tbars.dates[-253:]
    common = [(i, bench[dd]) for i, dd in enumerate(tdates) if dd in bench]
    if len(common) >= 120:
        idx = [i for i, _ in common]
        q = stats.summary(tclose[idx], [b for _, b in common])
    else:
        q = stats.summary(tclose)
    q["window"] = "1y"
    q["benchmark"] = BENCHMARK
    q["momentum"] = {k2: pf.get(k2) for k2 in ("RETURN_1M", "RETURN_3M", "RETURN_6M",
                                              "MOMENTUM_12_1", "DIST_SMA200")}
    out["quant"] = q
    # ----- fundamentals and valuation (filed on or before as_of)
    stmts = lake.nse_statements(sym, as_of) if mic == "XNSE" else []
    ff, prov = fx.fundamental_features(stmts, _f(tbars.close[-1]), as_of)
    end = prov["latest_period_end"]
    age = (as_of - date.fromisoformat(end)).days if end else None
    fstatus = ("MISSING" if not end else
               "STALE" if age is not None and age > policy.max_fundamental_age_days else "OK")
    # newest first; within a period the consolidated figures come first (preferred)
    quarters = sorted([r for r in stmts if r.get("period_type") == "Quarterly"],
                      key=lambda r: (str(r.get("to_date")),
                                     r.get("consolidated") == "Consolidated"))
    seen: set[str] = set()
    table = []
    for r in reversed(quarters):
        key = str(r.get("to_date"))[:10]
        if key in seen:
            continue
        seen.add(key)
        table.append({k3: r.get(k3) for k3 in (
            "to_date", "consolidated", "audited", "filing_datetime", "revenue_from_operations",
            "total_expenses", "profit_before_tax", "net_profit", "eps_basic", "eps_diluted")})
        if len(table) == 8:
            break
    out["fundamentals"] = {
        "status": fstatus, "latest_period_end": end, "latest_filed": prov.get("latest_filed"),
        "basis": prov.get("basis"), "age_days": age, "quarters": table,
        "metrics": {k4: ff.get(k4) for k4 in ("REVENUE_GROWTH_YOY", "EPS_GROWTH_YOY",
                                              "NET_MARGIN_TTM", "EPS_TTM")},
        "source": "NSE quarterly results (XBRL), as filed; restatements kept as new filings",
        "notes": ["NSE results XBRL carries the income statement only: balance sheet and cash "
                  "flow items (ROE, debt/equity, free cash flow) are not available"]}
    eps = ff.get("EPS_TTM")
    pe = (_f(tbars.close[-1]) or 0) / eps if eps and eps > 0 else None
    out["valuation"] = {
        "status": "OK" if pe and fstatus == "OK" else ("STALE" if pe else "UNSUPPORTED"),
        "pe_ttm": pe, "earnings_yield_ttm": ff.get("EARNINGS_YIELD_TTM"),
        "loss_making": bool(eps is not None and eps <= 0),
        "unsupported": {"pb": "no balance sheet in the data", "ev_ebitda": "no net debt data",
                        "dcf": "no cash-flow statement; assumptions would be unfounded"},
        "notes": ["P/E uses the last four reported quarters' basic EPS; it is not a fair value"]}
    # ----- events
    acts = lake.corporate_actions(iid, mic, as_of, bse_code=bse)
    evs = lake.events(sym, as_of - timedelta(days=400), as_of + timedelta(days=120))
    out["events"] = {"status": "OK", "corporate_actions": acts[-20:],
                     "calendar": [e for e in evs if str(e.get("event_date"))[:10] <= str(
                         as_of + timedelta(days=120))][-20:],
                     "notes": ["news is not available: no licensed news feed is connected"]}
    # ----- risk
    out["risk"] = {"status": q.get("status", "OK"), "var_95_1d": q.get("var_95_1d"),
                   "es_95_1d": q.get("es_95_1d"), "max_drawdown_1y": pf.get("MAX_DRAWDOWN_1Y"),
                   "downside_vol_1y": pf.get("DOWNSIDE_VOL_1Y"), "beta_1y": pf.get("BETA_1Y"),
                   "liquidity": {"adv_value_20d": pf.get("ADV_VALUE_20D"),
                                 "zero_volume_share_3m": pf.get("ZERO_VOLUME_SHARE_3M")},
                   "notes": ["historical one-day VaR/ES at 95%; portfolio impact needs a "
                             "portfolio (see /portfolios)"]}
    out["forecast"] = _forecast(session, p, iid)
    out["options"] = {"status": "UNSUPPORTED",
                      "reason": "options analytics need reliable option-chain data; not enabled"}
    return out


def _cmp(a: Any, b: Any) -> bool | None:
    x, y = _f(a), _f(b)
    return None if x is None or y is None else x > y


def _trend(adx: float | None) -> str | None:
    if adx is None:
        return None
    return "strong" if adx >= 25 else "weak" if adx < 20 else "moderate"


def _latest_decision(session: Session, p: Principal, iid: str) -> dict[str, Any] | None:
    r = session.scalar(select(DecisionRecord).join(
        AnalysisJob, AnalysisJob.id == DecisionRecord.job_id).where(
        DecisionRecord.workspace_id == p.workspace_id, DecisionRecord.instrument_id == iid)
        .order_by(DecisionRecord.as_of.desc(), DecisionRecord.created_at.desc()).limit(1))
    if r is None:
        return None
    return {"state": r.state, "opportunity": r.opportunity, "uncertainty": r.uncertainty,
            "as_of": r.as_of.isoformat(), "job_id": str(r.job_id), "pillars": r.pillars,
            "explanation": r.explanation, "review_by": r.review_by.isoformat()}


def _forecast(session: Session, p: Principal, iid: str) -> dict[str, Any]:
    """Latest stored prediction for this stock from an APPROVED model, per horizon."""
    from quantera.models_ml.models import MlModel, MlPrediction

    rows = session.execute(select(MlPrediction, MlModel).join(
        MlModel, MlModel.id == MlPrediction.model_id).where(
        MlPrediction.workspace_id == p.workspace_id, MlPrediction.instrument_id == iid,
        MlModel.stage == "APPROVED").order_by(MlPrediction.as_of.desc())).all()
    seen: dict[int, dict[str, Any]] = {}
    for pred, m in rows:
        if m.horizon in seen:
            continue
        seen[m.horizon] = {
            "horizon_days": m.horizon, "as_of": pred.as_of.isoformat(), "score": pred.score,
            "percentile": pred.percentile,
            "interval_90": [pred.interval_low, pred.interval_high]
            if pred.interval_low is not None else None,
            "meaning": "probability of beating NIFTY 50" if m.model_type == "LOGIT"
            else "expected excess return vs NIFTY 50",
            "model": {"id": str(m.id), "name": m.name, "version": m.version,
                      "type": m.model_type, "training_cutoff": m.label_cutoff.isoformat(),
                      "validation": m.gates.get("performance", {}).get("value"),
                      "calibration": m.validation.get("calibration", {}).get("ece")}}
    if not seen:
        return {"status": "MODEL UNAVAILABLE",
                "reason": "no approved model has scored this stock; nothing is shown in its place"}
    return {"status": "OK", "horizons": [seen[h] for h in sorted(seen)],
            "classification": "MODEL_OUTPUT",
            "note": "a forecast is uncertain; it is evidence, not a recommendation"}

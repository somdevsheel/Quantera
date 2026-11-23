"""Backtest jobs: strategy signal + point-in-time universe + lake data -> engine -> report,
with optional walk-forward over parameter sets and a deflated Sharpe ratio that counts every
backtest of the same family ever run in the workspace (multiple-testing control).

Universe:
* LIQUID (default): the top-N NSE EQ stocks by median traded value AT EACH REBALANCE DATE,
  delisted stocks included: no survivorship bias.
* SNAPSHOT: a saved universe snapshot. It is a list chosen today, so results carry a
  hindsight / survivorship warning, stated in the report.
"""

from __future__ import annotations

import bisect
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quant import backtest as bt
from quant import strategies as stg
from quantera.analysis.jobs import Cancelled, Progress
from quantera.analysis.models import AnalysisJob
from quantera.analysis.scan import BENCHMARK
from quantera.lake.client import LakeClient
from quantera.universes.models import UniverseSnapshot

MAX_NAMES = 600
WARMUP_DAYS = 420


class BacktestIn(BaseModel):
    name: str = Field(default="Backtest", min_length=1, max_length=120)
    signal: str
    signal_params: dict[str, Any] = Field(default_factory=dict)
    grid: list[dict[str, Any]] = Field(default_factory=list, max_length=8)  # walk-forward sets
    start: date
    end: date
    universe: Literal["LIQUID", "SNAPSHOT"] = "LIQUID"
    liquid_top: int = Field(default=100, ge=10, le=500)
    universe_snapshot_id: uuid.UUID | None = None
    rebalance: Literal["M", "Q"] = "M"
    selection: Literal["TOP_QUANTILE", "TOP_N"] = "TOP_QUANTILE"
    quantile: float = Field(default=0.2, gt=0, le=0.5)
    top_n: int = Field(default=20, ge=1, le=200)
    weighting: Literal["EQUAL", "INVERSE_VOL"] = "EQUAL"
    max_weight: float = Field(default=0.10, gt=0, le=1)
    capital: float = Field(default=10_000_000, gt=0)
    brokerage_bps: float = Field(default=3.0, ge=0, le=100)
    spread_bps: float = Field(default=10.0, ge=0, le=500)
    impact_coef: float = Field(default=0.1, ge=0, le=5)
    train_days: int = Field(default=504, ge=126, le=1500)
    test_days: int = Field(default=126, ge=21, le=504)

    @model_validator(mode="after")
    def _check(self) -> BacktestIn:
        if self.signal not in stg.SIGNALS:
            raise ValueError(f"unknown signal {self.signal}; one of {sorted(stg.SIGNALS)}")
        if self.end <= self.start + timedelta(days=90):
            raise ValueError("the backtest window must be longer than three months")
        if self.universe == "SNAPSHOT" and self.universe_snapshot_id is None:
            raise ValueError("SNAPSHOT universe needs universe_snapshot_id")
        return self


class LakeData:
    """Preloaded series; every accessor cuts at as_of (point in time)."""

    def __init__(self) -> None:
        self.dates: dict[str, list[date]] = {}
        self.close: dict[str, np.ndarray] = {}
        self.filings: dict[str, list[dict[str, Any]]] = {}

    def closes(self, name: str, as_of: date, n: int) -> np.ndarray:
        ds = self.dates.get(name)
        if not ds:
            return np.empty(0)
        k = bisect.bisect_right(ds, as_of)
        return self.close[name][max(0, k - n):k]

    def fundamentals(self, name: str, as_of: date) -> list[dict[str, Any]]:
        cut = datetime.combine(as_of, datetime.max.time())
        best: dict[tuple[str, str], dict[str, Any]] = {}
        for r in self.filings.get(name, []):
            fd = r.get("filing_datetime")
            if not fd or datetime.fromisoformat(str(fd)) > cut:
                continue  # not yet published at as_of
            k = (str(r.get("to_date")), str(r.get("consolidated")))
            if k not in best or str(fd) > str(best[k]["filing_datetime"]):
                best[k] = r
        return list(best.values())


def run(sessions: Callable[[], Session], lake: LakeClient, job_id: uuid.UUID) -> None:
    with sessions() as s:
        job = s.get(AnalysisJob, job_id)
        assert job is not None
        cfg = BacktestIn(**job.params["request"])
        snap_members = None
        if cfg.universe == "SNAPSHOT":
            snap = s.get(UniverseSnapshot, cfg.universe_snapshot_id)
            assert snap is not None
            snap_members = [m for m in snap.members if m.get("exchange") == "XNSE"]
        workspace_id = job.workspace_id
        family = stg.family_of(cfg.signal)
        prior = s.scalar(select(func.count()).select_from(AnalysisJob).where(
            AnalysisJob.workspace_id == workspace_id, AnalysisJob.kind == "BACKTEST",
            AnalysisJob.id != job_id, AnalysisJob.summary["family"].as_string() == family)) or 0
    prog = Progress(sessions, job_id)
    bench_rows = lake.index_daily(BENCHMARK, cfg.start, cfg.end)
    bench = {date.fromisoformat(str(r["trade_date"])[:10]): float(r["close"]) for r in bench_rows}
    days = sorted(bench)
    rebal = bt.rebalance_dates(days, cfg.rebalance)
    universe: dict[date, list[str]] = {}
    meta: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    if snap_members is not None:
        warnings.append("SURVIVORSHIP / HINDSIGHT: the universe is a list chosen today and is "
                        "applied to past dates; results are biased upwards")
        names = [m["instrument_id"] for m in snap_members]
        for m in snap_members:
            meta[m["instrument_id"]] = {"symbol": m["symbol"]}
        universe = {d: names for d in rebal}
    else:
        for d in rebal:
            mem = lake.liquid_universe(d, top=cfg.liquid_top)
            universe[d] = [r["instrument_id"] for r in mem.rows]
            for r in mem.rows:
                meta[r["instrument_id"]] = {"symbol": r["symbol"]}
    names = sorted({n for v in universe.values() for n in v})
    if len(names) > MAX_NAMES:
        raise ValueError(f"{len(names)} instruments over the window; the limit is {MAX_NAMES}")
    data = LakeData()
    prices: dict[str, dict[date, float]] = {}
    value: dict[str, dict[date, float]] = {}
    need_fund = cfg.signal in ("value_earnings_yield", "quality_margin", "composite")
    for i, n in enumerate(names, 1):
        _, rows = lake.prices_daily(instrument_id=n, exchange="XNSE",
                                    start=cfg.start - timedelta(days=WARMUP_DAYS), end=cfg.end,
                                    adjust="total", as_of=cfg.end)
        rows = [r for r in rows if r.get("close")]
        ds = [date.fromisoformat(str(r["trade_date"])[:10]) for r in rows]
        data.dates[n], data.close[n] = ds, np.array([float(r["close"]) for r in rows])
        prices[n] = dict(zip(ds, data.close[n], strict=True))
        value[n] = {dd: float(r.get("traded_value") or 0) for dd, r in zip(ds, rows, strict=True)}
        if need_fund:
            data.filings[n] = lake.nse_statements(meta[n]["symbol"], cfg.end, latest_only=False)
        if (i % 10 == 0 or i == len(names)) and not prog.update(i, len(names)):
            raise Cancelled
    costs = bt.Costs(brokerage_bps=cfg.brokerage_bps, spread_bps=cfg.spread_bps,
                     impact_coef=cfg.impact_coef)
    conf = bt.Config(rebalance=cfg.rebalance, selection=cfg.selection, quantile=cfg.quantile,
                     top_n=cfg.top_n, weighting=cfg.weighting, max_weight=cfg.max_weight,
                     capital=cfg.capital, costs=costs)
    sig = stg.SIGNALS[cfg.signal]

    def runner(params: dict[str, Any]) -> dict[str, Any]:
        return bt.run(days, prices, value, lambda d: universe.get(d, []),
                      lambda d, nm: sig(d, nm, data, **params), conf, bench)

    main = runner(cfg.signal_params)
    report: dict[str, Any] = {"family": family, "signal": cfg.signal, "result": main,
                              "warnings": warnings, "universe": {
                                  "kind": cfg.universe, "top": cfg.liquid_top,
                                  "instruments_over_window": len(names),
                                  "rebalances": len(rebal)}}
    trial_returns = [main["returns"]]
    if cfg.grid:
        runs = {f"set{i}": runner(g) for i, g in enumerate(cfg.grid)}
        trial_returns += [r["returns"] for r in runs.values()]
        report["walk_forward"] = bt.walk_forward(days, {k: v["returns"] for k, v in runs.items()},
                                                 cfg.train_days, cfg.test_days)
        report["walk_forward"]["grid"] = cfg.grid
    srs = [float(np.mean(r) / np.std(r, ddof=1)) for r in trial_returns if np.std(r) > 0]
    trials = prior + len(trial_returns)
    report["deflated_sharpe"] = bt.deflated_sharpe(main["returns"], trials,
                                                   srs if len(srs) > 1 else None)
    report["deflated_sharpe"]["note"] = (f"{prior} earlier backtest(s) of the {family} family in "
                                         "this workspace are counted as trials")
    main.pop("returns", None)
    with sessions() as s:
        j = s.get(AnalysisJob, job_id)
        assert j is not None
        j.summary = report
        j.versions = {"engine": bt.VERSION, "signal": cfg.signal}
        j.status = "SUCCEEDED"
        s.commit()

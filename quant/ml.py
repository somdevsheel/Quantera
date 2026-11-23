"""ML lifecycle core (pure): point-in-time dataset, purged walk-forward validation, baselines,
candidate models, promotion gates, calibration and drift.

Target: forward H-trading-day return of the stock minus the NIFTY 50 (excess return). A label
is MATURE only when H sessions have passed; immature rows are never used for training,
validation or monitoring.
Features (known at the close of the sample date; cross-sectional percentile ranks in [0, 1]):
  MOM_12_1, MOM_6_1, REV_1M, LOW_VOL_1Y, DIST_SMA200 (+ EY_TTM, MARGIN_TTM when fundamentals
  are supplied point in time). A row missing any feature is dropped and counted in coverage.
Validation: expanding walk-forward. For a test block starting at sample s, training uses only
samples whose label window ENDS before s minus the embargo (purge + embargo), so no training
label overlaps a test period.
Models compared out of sample (simple first; deep learning only after these are beaten):
  ZERO          no view (score 0)
  MOMENTUM      the MOM_12_1 rank alone
  RIDGE         linear regression with L2 (closed form) -> expected excess return
  LOGIT         L2 logistic regression -> probability of beating the benchmark (calibrated?)
  GBM           scikit-learn HistGradientBoostingRegressor (if installed)
An LLM is never a numeric predictor here.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
from numpy.typing import NDArray

F = NDArray[np.float64]
VERSION = "ml_v1"
PRICE_FEATURES = ("MOM_12_1", "MOM_6_1", "REV_1M", "LOW_VOL_1Y", "DIST_SMA200")
FUND_FEATURES = ("EY_TTM", "MARGIN_TTM")
MODELS = ("ZERO", "MOMENTUM", "RIDGE", "LOGIT", "GBM")
PSI_WARN, PSI_BLOCK = 0.10, 0.25


@dataclass
class Dataset:
    dates: list[date]  # sample date of each row
    names: list[str]
    sectors: list[str | None]
    x: F  # rows x features (percentile ranks)
    y: F  # forward excess return (mature labels only)
    features: tuple[str, ...]
    horizon: int
    coverage: float  # rows kept / rows considered

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        h.update(self.x.tobytes())
        h.update(self.y.tobytes())
        h.update(json.dumps([str(d) for d in self.dates] + self.names).encode())
        return h.hexdigest()[:16]


def raw_features(c: np.ndarray) -> dict[str, float | None]:
    """From total-return closes ending at the sample date."""
    def ret(back: int, skip: int = 0) -> float | None:
        return float(c[-1 - skip] / c[-1 - back] - 1) if len(c) > back else None
    lr = np.diff(np.log(c[-253:])) if len(c) > 200 else None
    return {"MOM_12_1": ret(252, 21), "MOM_6_1": ret(126, 21),
            "REV_1M": -r if (r := ret(21)) is not None else None,
            "LOW_VOL_1Y": -float(lr.std(ddof=1)) if lr is not None else None,
            "DIST_SMA200": float(c[-1] / c[-200:].mean() - 1) if len(c) >= 200 else None}


def ranks(values: Sequence[float | None]) -> list[float | None]:
    arr = np.array([np.nan if v is None else v for v in values], dtype=float)
    ok = np.isfinite(arr)
    out: list[float | None] = [None] * len(values)
    if ok.sum() < 5:
        return out
    r = arr[ok].argsort().argsort() / max(ok.sum() - 1, 1)
    for k, i in enumerate(np.flatnonzero(ok)):
        out[i] = float(r[k])
    return out


def build(samples: Sequence[dict[str, Any]], features: tuple[str, ...], horizon: int) -> Dataset:
    """``samples``: {date, name, sector, raw: {feature: value}, label: float | None}. Ranks are
    taken per date; rows with a missing feature or an immature label are dropped."""
    by_date: dict[date, list[dict[str, Any]]] = {}
    for s in samples:
        by_date.setdefault(s["date"], []).append(s)
    xs, ys, ds, ns, secs = [], [], [], [], []
    considered = 0
    for d in sorted(by_date):
        rows = by_date[d]
        considered += len(rows)
        rk = {f: ranks([r["raw"].get(f) for r in rows]) for f in features}
        for i, r in enumerate(rows):
            vec = [rk[f][i] for f in features]
            if r.get("label") is None or any(v is None for v in vec):
                continue
            xs.append(vec)
            ys.append(float(r["label"]))
            ds.append(d)
            ns.append(r["name"])
            secs.append(r.get("sector"))
    return Dataset(ds, ns, secs, np.array(xs, dtype=float).reshape(-1, len(features)),
                   np.array(ys, dtype=float), features, horizon,
                   len(ys) / considered if considered else 0.0)


# --------------------------------------------------------------------------- models
def fit(model: str, x: F, y: F, alpha: float = 10.0) -> dict[str, Any]:
    if model in ("ZERO", "MOMENTUM"):
        return {"model": model}
    if model == "RIDGE":
        xb = np.c_[np.ones(len(x)), x - 0.5]
        reg = alpha * np.eye(xb.shape[1])
        reg[0, 0] = 0
        return {"model": model, "coef": np.linalg.solve(xb.T @ xb + reg, xb.T @ y).tolist(),
                "resid_sd": float(np.std(y - xb @ np.linalg.solve(xb.T @ xb + reg, xb.T @ y)))}
    if model == "LOGIT":
        t = (y > 0).astype(float)
        xb = np.c_[np.ones(len(x)), x - 0.5]
        w = np.zeros(xb.shape[1])
        for _ in range(50):  # Newton with L2
            p = 1 / (1 + np.exp(-xb @ w))
            g = xb.T @ (p - t) + alpha * np.r_[0, w[1:]]
            ridge = alpha * np.diag(np.r_[0, np.ones(len(w) - 1)])
            hmat = xb.T @ (xb * (p * (1 - p))[:, None]) + ridge
            step = np.linalg.solve(hmat, g)
            w -= step
            if np.abs(step).max() < 1e-8:
                break
        return {"model": model, "coef": w.tolist()}
    if model == "GBM":
        from sklearn.ensemble import HistGradientBoostingRegressor

        m = HistGradientBoostingRegressor(max_depth=3, max_iter=150, learning_rate=0.05,
                                          l2_regularization=1.0, random_state=0)
        m.fit(x, y)
        return {"model": model, "estimator": m}
    raise ValueError(model)


def predict(fitted: dict[str, Any], x: F, features: tuple[str, ...]) -> F:
    m = fitted["model"]
    if m == "ZERO":
        return np.zeros(len(x))
    if m == "MOMENTUM":
        return x[:, features.index("MOM_12_1")] - 0.5
    if m in ("RIDGE", "LOGIT"):
        z = np.c_[np.ones(len(x)), x - 0.5] @ np.array(fitted["coef"])
        out: F = 1 / (1 + np.exp(-z)) if m == "LOGIT" else z
        return out
    pred: F = fitted["estimator"].predict(x)
    return pred


def rank_ic(pred: F, real: F) -> float | None:
    if len(pred) < 5 or np.std(pred) == 0 or np.std(real) == 0:
        return None
    a, b = pred.argsort().argsort(), real.argsort().argsort()
    return float(np.corrcoef(a, b)[0, 1])


@dataclass
class Validation:
    folds: list[dict[str, Any]] = field(default_factory=list)
    leakage_free: bool = True
    calibration: dict[str, Any] = field(default_factory=dict)
    by_year: dict[str, float] = field(default_factory=dict)
    by_sector: dict[str, float] = field(default_factory=dict)


def walk_forward(ds: Dataset, models: Sequence[str] = MODELS, n_folds: int = 5,
                 embargo_days: int = 5, cost_bps: float = 30.0) -> Validation:
    """Folds are blocks of sample dates. Training rows must have label windows ending before
    the test block starts minus the embargo (label end = sample date + horizon sessions,
    approximated in calendar days as horizon * 7/5)."""
    dates = sorted(set(ds.dates))
    if len(dates) < n_folds + 3:
        raise ValueError("not enough sample dates for walk-forward validation")
    v = Validation()
    lag_days = math.ceil(ds.horizon * 7 / 5) + embargo_days
    starts = [dates[len(dates) * (k + 1) // (n_folds + 1)] for k in range(n_folds)]
    dts = np.array([d.toordinal() for d in ds.dates])
    pooled_p: list[float] = []
    pooled_t: list[float] = []
    for k, s in enumerate(starts):
        end = starts[k + 1] if k + 1 < n_folds else dates[-1]
        train = dts + lag_days < s.toordinal()
        test = (dts >= s.toordinal()) & ((dts < end.toordinal()) if k + 1 < n_folds else True)
        if train.sum() < 200 or test.sum() < 20:
            continue
        if (dts[train] + lag_days).max() >= s.toordinal():
            v.leakage_free = False
        fold: dict[str, Any] = {"test_start": s.isoformat(), "test_end": end.isoformat(),
                                "train_rows": int(train.sum()), "test_rows": int(test.sum()),
                                "ic": {}, "spread_net": {}}
        for m in models:
            if m == "GBM":
                try:
                    import sklearn  # noqa: F401
                except ImportError:
                    continue
            f = fit(m, ds.x[train], ds.y[train])
            p = predict(f, ds.x[test], ds.features)
            tdates = dts[test]
            ics, spreads = [], []
            for d in np.unique(tdates):
                sel = tdates == d
                ic = rank_ic(p[sel], ds.y[test][sel])
                if ic is not None:
                    ics.append(ic)
                q = max(int(sel.sum() * 0.2), 1)
                order = p[sel].argsort()
                yy = ds.y[test][sel]
                spreads.append(float(yy[order[-q:]].mean() - yy[order[:q]].mean()
                                     - 2 * cost_bps / 1e4))
            fold["ic"][m] = float(np.mean(ics)) if ics else None
            fold["spread_net"][m] = float(np.mean(spreads)) if spreads else None
            if m == "LOGIT":
                pooled_p += p.tolist()
                pooled_t += (ds.y[test] > 0).astype(float).tolist()
        v.folds.append(fold)
    if pooled_p:
        v.calibration = calibration(np.array(pooled_p), np.array(pooled_t))
    return v


def calibration(p: F, t: F, bins: int = 10) -> dict[str, Any]:
    """Brier score and expected calibration error of probabilities ``p`` for outcomes ``t``."""
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    table, ece = [], 0.0
    for b in range(bins):
        sel = idx == b
        if sel.any():
            conf, acc = float(p[sel].mean()), float(t[sel].mean())
            ece += sel.mean() * abs(conf - acc)
            table.append({"bin": b, "mean_prob": conf, "hit_rate": acc, "n": int(sel.sum())})
    return {"brier": float(np.mean((p - t) ** 2)), "ece": float(ece), "reliability": table}


GATES = {
    "data": "coverage of considered rows >= 70%",
    "leakage": "purged + embargoed folds; no training label overlaps a test period",
    "enough_folds": "at least 3 evaluated folds",
    "performance": "mean out-of-sample rank IC >= best baseline + 0.01 and t-stat >= 2",
    "stability": ">= 60% of folds with positive IC",
    "net_of_cost": "mean top-minus-bottom quintile spread after costs > 0",
    "calibration": "for probability models: expected calibration error <= 0.05",
}


def gates(v: Validation, model: str, coverage: float) -> dict[str, dict[str, Any]]:
    ic = [f["ic"].get(model) for f in v.folds if f["ic"].get(model) is not None]
    base = max((float(np.mean(x)) for b in ("ZERO", "MOMENTUM")
                if (x := [f["ic"][b] for f in v.folds if f["ic"].get(b) is not None])),
               default=0.0)
    mean_ic = float(np.mean(ic)) if ic else float("nan")
    t = (mean_ic / (np.std(ic, ddof=1) / math.sqrt(len(ic)))
         if len(ic) >= 2 and np.std(ic, ddof=1) > 0 else float("nan"))
    pos = sum(x > 0 for x in ic) / len(ic) if ic else 0.0
    sp = [f["spread_net"].get(model) for f in v.folds if f["spread_net"].get(model) is not None]
    spread = float(np.mean(sp)) if sp else float("nan")
    ece = v.calibration.get("ece")
    vals: dict[str, tuple[Any, bool]] = {
        "data": (coverage, coverage >= 0.70),
        "leakage": (v.leakage_free, v.leakage_free),
        "enough_folds": (len(ic), len(ic) >= 3),
        "performance": ({"model_ic": mean_ic, "baseline_ic": base, "t_stat": t},
                        bool(math.isfinite(mean_ic) and mean_ic >= base + 0.01
                             and math.isfinite(t) and t >= 2)),
        "stability": (pos, pos >= 0.6),
        "net_of_cost": (spread, bool(math.isfinite(spread) and spread > 0)),
        "calibration": ((ece, ece is not None and ece <= 0.05) if model == "LOGIT"
                        else ("not a probability model", True)),
    }
    return {g: {"value": val, "passed": bool(ok), "rule": GATES[g]} for g, (val, ok) in
            vals.items()}


def reference(x: F, features: tuple[str, ...]) -> dict[str, list[float]]:
    """Decile edges of each feature's raw training distribution (for PSI drift)."""
    return {f: np.quantile(x[:, i], np.linspace(0, 1, 11)).tolist()
            for i, f in enumerate(features)}


def psi(edges: Sequence[float], actual: F) -> float | None:
    a = actual[np.isfinite(actual)]
    e = np.unique(np.asarray(edges))
    if len(a) < 10 or len(e) < 3:
        return None
    expected = np.full(len(e) - 1, 1 / (len(e) - 1))
    got = np.histogram(np.clip(a, e[0], e[-1]), e)[0] / len(a)
    got, expected = np.clip(got, 1e-4, None), np.clip(expected, 1e-4, None)
    return float(np.sum((got - expected) * np.log(got / expected)))

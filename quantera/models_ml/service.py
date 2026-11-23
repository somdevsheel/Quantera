"""ML governance: training jobs, registry, approval (four eyes), scoring with drift checks,
monitoring of realised outcomes, retirement and rollback.

Never promotes automatically: training ends as CANDIDATE (all gates passed) or REJECTED.
Scoring needs an APPROVED model; otherwise the answer is MODEL UNAVAILABLE. Dates inside the
training window are refused (in-sample). If any feature's PSI against the training
reference is >= 0.25 the scores are withheld (DRIFT ALERT).
"""

from __future__ import annotations

import bisect
import hashlib
import pickle
import subprocess
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quant import ml
from quantera.analysis.jobs import Cancelled, Progress
from quantera.analysis.models import AnalysisJob
from quantera.analysis.scan import BENCHMARK, latest_market_date
from quantera.core import audit
from quantera.core.config import ROOT
from quantera.core.errors import Conflict, DomainError, NotFound
from quantera.identity.service import Principal
from quantera.lake.client import LakeClient
from quantera.models_ml.models import MlModel, MlPrediction

ARTIFACTS = ROOT / "artifacts" / "models"
WARMUP_DAYS = 420


class TrainIn(BaseModel):
    name: str = Field(default="excess_return", min_length=1, max_length=80,
                      pattern=r"^[A-Za-z0-9_\-]+$")
    model_type: Literal["RIDGE", "LOGIT", "GBM"] = "RIDGE"
    horizon: Literal[5, 20, 60, 120] = 20
    start: date = date(2018, 1, 1)
    end: date | None = None
    liquid_top: int = Field(default=100, ge=20, le=300)
    n_folds: int = Field(default=5, ge=3, le=10)
    embargo_days: int = Field(default=5, ge=0, le=60)
    cost_bps: float = Field(default=30.0, ge=0, le=300)


class ScoreIn(BaseModel):
    name: str
    as_of: date | None = None
    liquid_top: int = Field(default=100, ge=20, le=500)


def _code_version() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,  # noqa: S607
                              capture_output=True, text=True, timeout=5,
                              check=False).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def _series(lake: LakeClient, iid: str, start: date, end: date) -> tuple[list[date], np.ndarray]:
    _, rows = lake.prices_daily(instrument_id=iid, exchange="XNSE", start=start, end=end,
                                adjust="total", as_of=end)
    rows = [r for r in rows if r.get("close")]
    return ([date.fromisoformat(str(r["trade_date"])[:10]) for r in rows],
            np.array([float(r["close"]) for r in rows]))


def _upto(ds: list[date], c: np.ndarray, d: date) -> np.ndarray:
    return c[:bisect.bisect_right(ds, d)]


def train(sessions: Callable[[], Session], lake: LakeClient, job_id: uuid.UUID) -> None:
    with sessions() as s:
        job = s.get(AnalysisJob, job_id)
        assert job is not None
        cfg = TrainIn(**job.params["request"])
        ws, creator = job.workspace_id, job.created_by
    prog = Progress(sessions, job_id)
    end = cfg.end or latest_market_date(lake, date.today())
    bench = {date.fromisoformat(str(r["trade_date"])[:10]): float(r["close"])
             for r in lake.index_daily(BENCHMARK, cfg.start - timedelta(days=WARMUP_DAYS), end)}
    bdays = sorted(bench)
    sample_dates = [d for i, d in enumerate(bdays[:-1]) if d >= cfg.start
                    and bdays[i + 1].month != d.month]
    members = {d: lake.liquid_universe(d, top=cfg.liquid_top).rows for d in sample_dates}
    names = sorted({r["instrument_id"] for v in members.values() for r in v})
    data: dict[str, tuple[list[date], np.ndarray]] = {}
    for i, n in enumerate(names, 1):
        data[n] = _series(lake, n, cfg.start - timedelta(days=WARMUP_DAYS), end)
        if (i % 20 == 0 or i == len(names)) and not prog.update(i, len(names)):
            raise Cancelled
    samples, raw_rows = [], []
    for d in sample_dates:
        k = bdays.index(d)
        horizon_day = bdays[k + cfg.horizon] if k + cfg.horizon < len(bdays) else None
        for r in members[d]:
            ds, c = data[r["instrument_id"]]
            hist = _upto(ds, c, d)
            if len(hist) == 0 or ds[len(hist) - 1] != d:
                continue
            raw = ml.raw_features(hist)
            label = None
            if horizon_day is not None:  # mature only
                fut = _upto(ds, c, horizon_day)
                if len(fut) and ds[len(fut) - 1] == horizon_day:
                    label = (fut[-1] / hist[-1] - 1) - (bench[horizon_day] / bench[d] - 1)
            samples.append({"date": d, "name": r["instrument_id"], "sector": None, "raw": raw,
                            "label": label})
            if None not in raw.values():
                raw_rows.append([raw[f] for f in ml.PRICE_FEATURES])
    dset = ml.build(samples, ml.PRICE_FEATURES, cfg.horizon)
    if len(dset.y) < 500:
        raise ValueError(f"only {len(dset.y)} mature rows; widen the window")
    compare = ("ZERO", "MOMENTUM", "RIDGE", "LOGIT", "GBM")
    val = ml.walk_forward(dset, compare, cfg.n_folds, cfg.embargo_days, cfg.cost_bps)
    gates = ml.gates(val, cfg.model_type, dset.coverage)
    passed = all(g["passed"] for g in gates.values())
    fitted = ml.fit(cfg.model_type, dset.x, dset.y)
    label_cutoff = max(dset.dates)
    with sessions() as s:
        version = (s.scalar(select(func.max(MlModel.version)).where(
            MlModel.workspace_id == ws, MlModel.name == cfg.name)) or 0) + 1
        mid = uuid.uuid4()
        path = ARTIFACTS / str(ws) / f"{mid}.pkl"
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = pickle.dumps(fitted)
        path.write_bytes(blob)
        m = MlModel(
            id=mid, workspace_id=ws, created_by=creator, job_id=job_id, name=cfg.name,
            version=version, stage="CANDIDATE" if passed else "REJECTED",
            model_type=cfg.model_type,
            target=f"{cfg.horizon}-session excess return vs NIFTY 50", horizon=cfg.horizon,
            features=list(ml.PRICE_FEATURES), params=cfg.model_dump(mode="json"),
            dataset={"fingerprint": dset.fingerprint(), "rows": len(dset.y),
                     "coverage": dset.coverage, "sample_dates": len(set(dset.dates)),
                     "start": min(dset.dates).isoformat(), "end": label_cutoff.isoformat(),
                     "universe": f"point-in-time top {cfg.liquid_top} NSE EQ by traded value",
                     "lake_release": str(lake.health().get("release"))},
            validation={"folds": val.folds, "calibration": val.calibration,
                        "leakage_free": val.leakage_free, "models_compared": list(compare),
                        "cost_bps": cfg.cost_bps},
            gates=gates, reference=ml.reference(np.array(raw_rows), ml.PRICE_FEATURES),
            code_version=_code_version(), artifact_path=str(path.relative_to(ROOT)),
            artifact_sha256=hashlib.sha256(blob).hexdigest(), label_cutoff=label_cutoff,
            history=[{"event": "TRAINED", "at": datetime.now(UTC).isoformat(),
                      "note": "all gates passed" if passed else "rejected: " + ", ".join(
                          k for k, g in gates.items() if not g["passed"])}])
        s.add(m)
        j = s.get(AnalysisJob, job_id)
        assert j is not None
        j.summary = {"model_id": str(mid), "stage": m.stage, "version": version,
                     "gates": {k: g["passed"] for k, g in gates.items()}}
        j.status = "SUCCEEDED"
        s.commit()


def load(m: MlModel) -> dict[str, Any]:
    blob = (ROOT / m.artifact_path).read_bytes()
    if hashlib.sha256(blob).hexdigest() != m.artifact_sha256:
        raise DomainError("Model artifact checksum mismatch: refusing to load")
    out: dict[str, Any] = pickle.loads(blob)  # noqa: S301 - checksum-verified own artifact
    return out


def get(session: Session, p: Principal, mid: uuid.UUID) -> MlModel:
    m = session.get(MlModel, mid)
    if m is None or m.workspace_id != p.workspace_id:
        raise NotFound("Model not found")
    return m


def champion(session: Session, p: Principal, name: str) -> MlModel | None:
    return session.scalar(select(MlModel).where(MlModel.workspace_id == p.workspace_id,
                                                MlModel.name == name, MlModel.stage == "APPROVED"))


def _log(m: MlModel, p: Principal, event: str, note: str) -> None:
    m.history = [*m.history, {"event": event, "by": str(p.user_id),
                              "at": datetime.now(UTC).isoformat(), "note": note}]


def approve(session: Session, p: Principal, m: MlModel, note: str, four_eyes: bool) -> MlModel:
    if m.stage != "CANDIDATE":
        raise Conflict(f"Only a CANDIDATE that passed every gate can be approved ({m.stage})")
    if four_eyes and m.created_by == p.user_id:
        raise Conflict("Four-eyes rule: a model must be approved by someone other than the "
                       "person who trained it")
    try:
        load(m)  # operational gate: the artifact exists and its checksum matches
    except (OSError, DomainError) as exc:
        raise Conflict(f"Operational gate failed: {exc}") from exc
    old = champion(session, p, m.name)
    if old is not None:
        old.stage = "RETIRED"
        _log(old, p, "SUPERSEDED", f"replaced by version {m.version}")
    m.stage, m.approved_by, m.approved_at = "APPROVED", p.user_id, datetime.now(UTC)
    _log(m, p, "APPROVED", note)
    audit.record(session, "model.approve", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="ml_model", resource_id=m.id, details={"note": note})
    return m


def retire(session: Session, p: Principal, m: MlModel, note: str) -> MlModel:
    if m.stage not in ("APPROVED", "CANDIDATE"):
        raise Conflict(f"A {m.stage} model cannot be retired")
    m.stage = "RETIRED"
    _log(m, p, "RETIRED", note)
    audit.record(session, "model.retire", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="ml_model", resource_id=m.id, details={"note": note})
    return m


def rollback(session: Session, p: Principal, name: str, note: str) -> dict[str, Any]:
    cur = champion(session, p, name)
    if cur is None:
        raise Conflict(f"No APPROVED model named {name}")
    cur.stage = "RETIRED"
    _log(cur, p, "ROLLED_BACK", note)
    prev = session.scalar(select(MlModel).where(
        MlModel.workspace_id == p.workspace_id, MlModel.name == name, MlModel.id != cur.id,
        MlModel.stage == "RETIRED", MlModel.approved_at.is_not(None))
        .order_by(MlModel.approved_at.desc()).limit(1))
    if prev is not None:
        prev.stage = "APPROVED"
        _log(prev, p, "RESTORED", f"rollback from version {cur.version}: {note}")
    audit.record(session, "model.rollback", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="ml_model", resource_id=cur.id,
                 details={"restored": str(prev.id) if prev else None})
    return {"retired": str(cur.id), "restored": str(prev.id) if prev else None,
            "state": "APPROVED" if prev else "MODEL UNAVAILABLE"}


def score(session: Session, p: Principal, lake: LakeClient, body: ScoreIn) -> dict[str, Any]:
    m = champion(session, p, body.name)
    if m is None:
        return {"state": "MODEL UNAVAILABLE", "model_name": body.name, "rows": [],
                "reason": "no APPROVED model with this name; nothing is scored without one"}
    as_of = body.as_of or latest_market_date(lake, date.today())
    if as_of <= m.label_cutoff:
        raise DomainError(f"as_of {as_of} is inside the training window (to {m.label_cutoff}): "
                          "scores there would be in-sample")
    fitted = load(m)
    mem = lake.liquid_universe(as_of, top=body.liquid_top).rows
    raws, keep = [], []
    for r in mem:
        ds, c = _series(lake, r["instrument_id"], as_of - timedelta(days=WARMUP_DAYS), as_of)
        if len(c) == 0 or ds[-1] != as_of:
            continue
        raw = ml.raw_features(c)
        if None in raw.values():
            continue
        raws.append([raw[f] for f in m.features])
        keep.append(r)
    if len(keep) < 5:
        return {"state": "INSUFFICIENT DATA", "model_name": body.name, "rows": []}
    xr = np.array(raws)
    drift = {f: ml.psi(m.reference[f], xr[:, i]) for i, f in enumerate(m.features)}
    worst = max((v for v in drift.values() if v is not None), default=0.0)
    base = {"model_id": str(m.id), "model_name": m.name, "version": m.version,
            "as_of": as_of.isoformat(), "horizon": m.horizon, "classification": "MODEL_OUTPUT",
            "training_cutoff": m.label_cutoff.isoformat(),
            "drift": {"psi": drift, "warn_at": ml.PSI_WARN, "block_at": ml.PSI_BLOCK}}
    if worst >= ml.PSI_BLOCK:
        return base | {"state": "DRIFT ALERT", "rows": [],
                       "reason": "feature distribution moved too far from training; retrain "
                                 "or roll back before using scores"}
    x = np.column_stack([np.array(ml.ranks(list(xr[:, i]))) for i in range(xr.shape[1])])
    pred = ml.predict(fitted, x, tuple(m.features))
    pct = pred.argsort().argsort() / max(len(pred) - 1, 1) * 100
    sd = fitted.get("resid_sd")
    matures = as_of + timedelta(days=int(m.horizon * 7 / 5) + 1)
    rows = []
    for i, r in enumerate(keep):
        lo = float(pred[i] - 1.645 * sd) if sd and m.model_type == "RIDGE" else None
        hi = float(pred[i] + 1.645 * sd) if sd and m.model_type == "RIDGE" else None
        session.add(MlPrediction(workspace_id=p.workspace_id, model_id=m.id, as_of=as_of,
                                 instrument_id=r["instrument_id"], symbol=r["symbol"],
                                 score=float(pred[i]), percentile=float(pct[i]),
                                 interval_low=lo, interval_high=hi, matures_on=matures))
        rows.append({"symbol": r["symbol"], "score": float(pred[i]), "percentile": float(pct[i]),
                     "interval_90": [lo, hi] if lo is not None else None})
    rows.sort(key=lambda x: -x["score"])
    notes = ["moderate drift: use with extra caution"] if worst >= ml.PSI_WARN else []
    return base | {"state": "SCORED", "rows": rows, "notes": notes,
                   "meaning": "LOGIT = probability of beating NIFTY 50 over the horizon; others "
                              "= expected excess return",
                   "disclaimer": "Model output, not a forecast guarantee or advice."}


def monitor(session: Session, p: Principal, lake: LakeClient, m: MlModel) -> dict[str, Any]:
    """Realised outcomes of stored predictions whose horizon has passed."""
    latest = latest_market_date(lake, date.today())
    preds = list(session.scalars(select(MlPrediction).where(
        MlPrediction.model_id == m.id, MlPrediction.matures_on <= latest)))
    by_date: dict[date, list[MlPrediction]] = {}
    for x in preds:
        by_date.setdefault(x.as_of, []).append(x)
    bench = {date.fromisoformat(str(r["trade_date"])[:10]): float(r["close"])
             for r in lake.index_daily(BENCHMARK, min(by_date, default=latest), latest)}
    bdays = sorted(bench)
    out = []
    for d, rows in sorted(by_date.items()):
        k = bisect.bisect_left(bdays, d)
        if k + m.horizon >= len(bdays):
            continue
        hd = bdays[k + m.horizon]
        real, sc = [], []
        for x in rows:
            if x.realised is None:
                ds, c = _series(lake, x.instrument_id, d, hd)
                if len(c) >= 2 and ds[0] == d and ds[-1] == hd:
                    x.realised = float((c[-1] / c[0] - 1) - (bench[hd] / bench[d] - 1))
            if x.realised is not None:
                real.append(x.realised)
                sc.append(x.score)
        ic = ml.rank_ic(np.array(sc), np.array(real)) if len(sc) >= 5 else None
        out.append({"as_of": d.isoformat(), "matured_on": hd.isoformat(), "n": len(real),
                    "realised_rank_ic": ic,
                    "validation_ic": m.gates.get("performance", {}).get("value", {}).get(
                        "model_ic")})
    return {"model_id": str(m.id), "stage": m.stage, "matured_batches": out,
            "pending": sum(1 for x in session.scalars(select(MlPrediction.id).where(
                MlPrediction.model_id == m.id, MlPrediction.matures_on > latest))),
            "note": "compare realised IC with the validation IC; persistent shortfall or drift "
                    "alerts call for retraining or rollback"}


def summary(m: MlModel) -> dict[str, Any]:
    return {"id": str(m.id), "name": m.name, "version": m.version, "stage": m.stage,
            "model_type": m.model_type, "target": m.target, "horizon": m.horizon,
            "created_at": m.created_at.isoformat(), "label_cutoff": m.label_cutoff.isoformat(),
            "gates": {k: g["passed"] for k, g in m.gates.items()},
            "performance": m.gates.get("performance", {}).get("value"),
            "code_version": m.code_version, "dataset": m.dataset, "history": m.history}


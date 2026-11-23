"""ML core: point-in-time labels, purged walk-forward, gates, calibration, drift."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from quant import ml


def samples(signal=0.0, n_dates=60, n_names=40, seed=0, immature_last=2):
    rng = np.random.default_rng(seed)
    out = []
    for k in range(n_dates):
        d = date(2018, 1, 31) + timedelta(days=30 * k)
        for j in range(n_names):
            raw = {f: float(rng.normal()) for f in ml.PRICE_FEATURES}
            label = signal * (raw["MOM_12_1"] + raw["REV_1M"]) + float(rng.normal(0, 1))
            out.append({"date": d, "name": f"S{j}", "sector": "IT" if j % 2 else "Bank",
                        "raw": raw, "label": None if k >= n_dates - immature_last else label})
    return out


def test_build_drops_immature_and_ranks_per_date():
    ds = ml.build(samples(), ml.PRICE_FEATURES, 20)
    assert len(set(ds.dates)) == 58  # the last two dates have no mature labels
    assert ds.x.min() >= 0 and ds.x.max() <= 1 and ds.coverage < 1
    assert len(ds.fingerprint()) == 16


def test_planted_signal_passes_and_noise_fails():
    good = ml.build(samples(signal=0.3), ml.PRICE_FEATURES, 20)
    v = ml.walk_forward(good, models=("ZERO", "MOMENTUM", "RIDGE", "LOGIT"))
    assert v.leakage_free and len(v.folds) >= 3
    g = ml.gates(v, "RIDGE", good.coverage)
    assert all(x["passed"] for x in g.values()), g  # two-feature signal: ridge beats baselines
    only_mom = ml.gates(v, "MOMENTUM", good.coverage)
    assert not only_mom["performance"]["passed"]  # a baseline cannot beat itself
    noise = ml.build(samples(signal=0.0, seed=3), ml.PRICE_FEATURES, 20)
    vn = ml.walk_forward(noise, models=("ZERO", "MOMENTUM", "RIDGE"))
    assert not all(x["passed"] for x in ml.gates(vn, "RIDGE", 1.0).values())


def test_purge_keeps_labels_out_of_test_blocks():
    ds = ml.build(samples(n_dates=40), ml.PRICE_FEATURES, 60)  # long horizon
    v = ml.walk_forward(ds, models=("ZERO", "RIDGE"))
    for f in v.folds:
        assert f["train_rows"] > 0
    assert v.leakage_free


def test_calibration_and_psi():
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, 5000)
    t = (rng.uniform(0, 1, 5000) < p).astype(float)  # perfectly calibrated
    assert ml.calibration(p, t)["ece"] < 0.03
    bad = ml.calibration(np.clip(p * 0.2 + 0.8, 0, 1), t)
    assert bad["ece"] > 0.2
    x = rng.normal(0, 1, (1000, 1))
    ref = ml.reference(x, ("A",))["A"]
    assert ml.psi(ref, rng.normal(0, 1, 500)) < ml.PSI_WARN
    assert ml.psi(ref, rng.normal(2, 0.3, 500)) >= ml.PSI_BLOCK


def test_models_fit_and_predict_shapes():
    ds = ml.build(samples(signal=0.3), ml.PRICE_FEATURES, 20)
    for m in ("ZERO", "MOMENTUM", "RIDGE", "LOGIT", "GBM"):
        f = ml.fit(m, ds.x, ds.y)
        p = ml.predict(f, ds.x[:10], ds.features)
        assert p.shape == (10,)
    lg = ml.predict(ml.fit("LOGIT", ds.x, ds.y), ds.x, ds.features)
    assert (lg >= 0).all() and (lg <= 1).all()

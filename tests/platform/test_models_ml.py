"""ML governance on real lake data: train, gates, four eyes, scoring, forecast, monitor,
rollback. No automatic promotion anywhere."""

from __future__ import annotations

import pytest

from quant import ml
from quantera.workers.runner import run_until_empty

M = "/api/v1/models"
TRAIN = {"start": "2020-01-01", "liquid_top": 40, "horizon": 20}


def train(client, h, lake, **kw):
    r = client.post(f"{M}/train", headers=h, json=TRAIN | kw)
    assert r.status_code == 202, r.text
    run_until_empty(client.app.state.db.sessions, lake)
    job = client.get(f"/api/v1/analysis/jobs/{r.json()['id']}", headers=h).json()
    assert job["status"] == "SUCCEEDED", job["error"]
    return client.get(f"{M}/{job['summary']['model_id']}", headers=h).json()


@pytest.fixture
def gates_pass(monkeypatch):
    """Real gates decide on real data (usually REJECTED). To test the approval workflow
    itself, this stands in for a model that passed every gate."""
    real = ml.gates

    def passed(v, model, coverage):
        return {k: g | {"passed": True} for k, g in real(v, model, coverage).items()}
    monkeypatch.setattr(ml, "gates", passed)


def test_real_training_is_gated_and_reproducible(client, user, lake):
    m = train(client, user["headers"], lake)
    assert m["stage"] in ("CANDIDATE", "REJECTED") and m["version"] == 1
    v = m["validation"]
    assert v["leakage_free"] and len(v["folds"]) >= 3
    assert set(v["folds"][0]["ic"]) >= {"ZERO", "MOMENTUM", "RIDGE"}
    assert m["dataset"]["fingerprint"] and m["dataset"]["end"] < "2026-11-06"  # mature labels
    assert len(m["artifact_sha256"]) == 64 and m["code_version"]
    assert set(m["gates"]) == set(ml.GATES)
    if m["stage"] == "REJECTED":
        r = client.post(f"{M}/{m['id']}/approve", headers=user["headers"],
                        json={"note": "looks fine"})
        assert r.status_code == 409  # a rejected model can never be approved


def test_governance_flow(client, user, lake, gates_pass):
    h = user["headers"]
    first = train(client, h, lake)
    assert first["stage"] == "CANDIDATE"  # never promoted automatically
    sc = client.post(f"{M}/score", headers=h, json={"name": "excess_return"}).json()
    assert sc["state"] == "MODEL UNAVAILABLE" and sc["rows"] == []
    url = f"{M}/{first['id']}/approve"
    r = client.post(url, headers=h, json={"note": "reviewed"})
    assert r.status_code == 409 and "Four-eyes" in r.text
    client.app.state.settings.ml_four_eyes = False
    assert client.post(url, headers=h, json={"note": "reviewed"}).json()["stage"] == "APPROVED"
    bad = client.post(f"{M}/score", headers=h, json={"name": "excess_return",
                                                     "as_of": "2023-06-30"})
    assert bad.status_code == 400 and "in-sample" in bad.text
    sc = client.post(f"{M}/score", headers=h, json={"name": "excess_return",
                                                    "liquid_top": 40}).json()
    assert sc["state"] in ("SCORED", "DRIFT ALERT") and sc["classification"] == "MODEL_OUTPUT"
    if sc["state"] == "SCORED":
        scores = [x["score"] for x in sc["rows"]]
        assert scores == sorted(scores, reverse=True)
        sym = sc["rows"][0]["symbol"]
        fc = client.get(f"/api/v1/stocks/NSE/{sym}", headers=h).json()["forecast"]
        assert fc["status"] == "OK" and fc["horizons"][0]["horizon_days"] == 20
        mon = client.get(f"{M}/{first['id']}/monitor", headers=h).json()
        assert mon["pending"] >= len(sc["rows"])  # labels not matured yet
    second = train(client, h, lake, model_type="LOGIT")
    client.post(f"{M}/{second['id']}/approve", headers=h, json={"note": "version two"})
    reg = {m["id"]: m["stage"] for m in client.get(M, headers=h).json()}
    assert reg == {first["id"]: "RETIRED", second["id"]: "APPROVED"}
    rb = client.post(f"{M}/rollback", headers=h, params={"name": "excess_return"},
                     json={"note": "v2 degraded"}).json()
    assert rb == {"retired": second["id"], "restored": first["id"], "state": "APPROVED"}
    hist = [e["event"] for e in client.get(f"{M}/{first['id']}", headers=h).json()["history"]]
    assert hist == ["TRAINED", "APPROVED", "SUPERSEDED", "RESTORED"]
    client.post(f"{M}/{first['id']}/retire", headers=h, json={"note": "stop using it"})
    assert client.get(f"{M}/active", headers=h, params={"name": "excess_return"}
                      ).json()["state"] == "MODEL UNAVAILABLE"


def test_forecast_section_without_model(client, user):
    fc = client.get("/api/v1/stocks/NSE/TCS", headers=user["headers"]).json()["forecast"]
    assert fc["status"] == "MODEL UNAVAILABLE"


def test_validation_and_auth(client, user):
    h = user["headers"]
    assert client.post(f"{M}/train", headers=h, json={"horizon": 7}).status_code == 422
    assert client.post(f"{M}/train", headers=h, json={"name": "bad name!"}).status_code == 422
    assert client.get(M).status_code == 401

"""Portfolio construction and trade plans on real lake data."""

from __future__ import annotations

import pytest

from quantera.workers.runner import run_until_empty
from tests.platform.conftest import register

C, T = "/api/v1/portfolio-construction", "/api/v1/trade-plans"
NAMES = ["TCS", "INFY", "HCLTECH", "WIPRO", "RELIANCE", "ONGC", "HDFCBANK", "ICICIBANK",
         "SBIN", "ITC", "HINDUNILVR", "NESTLEIND"]
PANEL = {"current_price", "stop_loss", "target_price", "trailing_stop_loss"}


def build(client, h, lake, **kw):
    u = client.post("/api/v1/universes", headers=h, json={
        "name": f"u{len(kw)}", "kind": "MANUAL", "symbols": NAMES}).json()
    r = client.post(f"{C}/jobs", headers=h, json={"universe_id": u["id"]} | kw)
    assert r.status_code == 202, r.text
    run_until_empty(client.app.state.db.sessions, lake)
    job = client.get(f"{C}/jobs/{r.json()['id']}", headers=h).json()
    assert job["status"] == "SUCCEEDED", job["error"]
    return job


def test_long_short_neutral_book(client, user, lake):
    job = build(client, user["headers"], lake, shortable=NAMES, gross_max=1.0, max_weight=0.2)
    prop = job["summary"]["proposal"]
    assert prop["status"] in ("OPTIMAL", "APPROXIMATE") and prop["weights_rounded"]
    e = prop["exposures"]
    assert abs(e["net"]) <= 0.02 + 1e-6 and abs(e["beta"]) <= 0.05 + 1e-6
    assert prop["all_checks_pass"] and "DOLLAR_NEUTRAL" in prop["label"]
    assert {leg["side"] for leg in prop["legs"]} == {"LONG", "SHORT"}
    assert all(leg["shares"] == int(leg["shares"]) for leg in prop["legs"])
    assert prop["estimation"]["observations"] > 200 and prop["signal"]["classification"] == \
        "MODEL_OUTPUT"
    assert any("squeeze" in s["scenario"] for s in prop["stress"])
    assert prop["stop_risk"]["all_stops_hit"] > 0


def test_nothing_shortable_is_infeasible_with_alternatives(client, user, lake):
    prop = build(client, user["headers"], lake)["summary"]["proposal"]
    assert prop["status"] == "INFEASIBLE" and "INDEX_HEDGED" in prop["alternatives"]
    assert any("SLB" in n for n in prop["notes"])


def test_index_hedged_simulate_and_approve(client, user, lake):
    h = user["headers"]
    job = build(client, h, lake, mode="INDEX_HEDGED", as_of="2025-06-30", gross_max=1.0,
                max_weight=0.2)
    prop = job["summary"]["proposal"]
    assert "INDEX_HEDGED" in prop["label"] and prop["hedge"]["weight"] < 0
    assert all(leg["side"] == "LONG" for leg in prop["legs"])
    sim = client.post(f"{C}/{job['id']}/simulate", headers=h).json()
    assert sim["status"] == "OK" and sim["out_of_sample"] and sim["start"] >= "2025-06-30"
    assert len(sim["curve"]) > 50
    pf = client.post("/api/v1/portfolios", headers=h, json={"name": "Paper", "is_paper": True}
                     ).json()
    client.post(f"/api/v1/portfolios/{pf['id']}/transactions", headers=h, json=[
        {"trade_date": "2025-01-02", "type": "DEPOSIT", "amount": 5_000_000}])
    need = client.post(f"{C}/{job['id']}/approve-paper", headers=h,
                       json={"note": "reviewed", "paper_portfolio_id": pf["id"]})
    assert need.status_code == 409  # explicit confirmation required
    ok = client.post(f"{C}/{job['id']}/approve-paper", headers=h,
                     json={"note": "reviewed", "paper_portfolio_id": pf["id"], "confirm": True})
    assert ok.status_code == 200 and ok.json()["live_trading"] == "disabled"
    assert len(ok.json()["booked"]) == len(prop["legs"])
    again = client.post(f"{C}/{job['id']}/approve-paper", headers=h,
                        json={"note": "second try", "confirm": True})
    assert again.status_code == 409
    assert client.get(f"{C}/jobs/{job['id']}", headers=h).json()["approval"]["status"] == \
        "APPROVED_PAPER"


def test_long_short_cannot_be_booked_in_cash_paper_book(client, user, lake):
    h = user["headers"]
    job = build(client, h, lake, shortable=NAMES, gross_max=1.0, max_weight=0.2)
    pf = client.post("/api/v1/portfolios", headers=h, json={"name": "P2", "is_paper": True}).json()
    r = client.post(f"{C}/{job['id']}/approve-paper", headers=h,
                    json={"note": "try booking", "confirm": True, "paper_portfolio_id": pf["id"]})
    assert r.status_code == 400 and "Short legs" in r.json()["error"]["message"]


# --------------------------------------------------------------------------- trade plans
def test_preview_has_exactly_four_fields(client, user):
    r = client.post(f"{T}/preview", headers=user["headers"], json={"symbol": "TCS"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d["price_levels"]) == PANEL
    lv = d["price_levels"]
    assert lv["stop_loss"]["price"] < lv["current_price"] < lv["target_price"]["price"]
    assert lv["stop_loss"]["state"] == "SUGGESTED"
    text = str(d["price_levels"]).lower()
    assert not any(w in text for w in ("capital", "quantity", "margin", "equity"))
    short = client.post(f"{T}/preview", headers=user["headers"],
                        json={"symbol": "TCS", "side": "SHORT"}).json()["price_levels"]
    assert short["stop_loss"]["price"] > short["current_price"] > short["target_price"]["price"]


@pytest.fixture
def paper(client, user):
    h = user["headers"]
    pf = client.post("/api/v1/portfolios", headers=h, json={"name": "Plans", "is_paper": True}
                     ).json()
    client.post(f"/api/v1/portfolios/{pf['id']}/transactions", headers=h, json=[
        {"trade_date": "2024-12-02", "type": "DEPOSIT", "amount": 1_000_000}])
    return pf["id"]


def test_replay_plan_books_entry_and_oco_exits(client, user, lake, paper):
    h = user["headers"]
    r = client.post(T, headers=h, json={
        "symbol": "TCS", "as_of": "2025-01-01", "portfolio_id": paper, "quantity": 10,
        "book_entry": True, "trailing": None, "stop": {"method": "PERCENT", "value": 0.03},
        "targets": [{"method": "RISK_REWARD", "value": 1.0, "fraction": 0.5},
                    {"method": "RISK_REWARD", "value": 3.0, "fraction": 0.5}],
        "client_ref": "tcs-replay", "activate": True})
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["status"] == "ACTIVE" and plan["price_levels"]["stop_loss"]["state"] == "ACTIVE"
    ev = client.post(f"{T}/{plan['id']}/evaluate", headers=h, json={}).json()
    fills = [e for e in ev["events"] if e["event"] == "FILL"]
    assert fills, "a 3% stop / 1R target on TCS over 2025-2026 must trigger"
    assert ev["status"] in ("CLOSED", "ACTIVE")
    txs = client.get(f"/api/v1/portfolios/{paper}/transactions", headers=h).json()
    sells = [t for t in txs if t["type"] == "SELL" and t["source"] == "trade_plan"]
    assert sorted(t["quantity"] for t in sells) == sorted(f["quantity"] for f in fills)
    n = len(ev["events"])
    again = client.post(f"{T}/{plan['id']}/evaluate", headers=h, json={}).json()
    assert len(again["events"]) == n  # idempotent
    same = client.post(T, headers=h, json={
        "symbol": "TCS", "as_of": "2025-01-01", "portfolio_id": paper, "quantity": 10,
        "book_entry": True, "trailing": None, "stop": {"method": "PERCENT", "value": 0.03},
        "targets": [{"method": "RISK_REWARD", "value": 1.0, "fraction": 0.5},
                    {"method": "RISK_REWARD", "value": 3.0, "fraction": 0.5}],
        "client_ref": "tcs-replay", "activate": True})
    assert same.json()["id"] == plan["id"]  # idempotent create
    assert client.get(f"{T}/alerts", headers=h).json()


def test_edit_rules_and_lifecycle(client, user):
    h = user["headers"]
    p = client.post(T, headers=h, json={"symbol": "INFY"}).json()
    stop = p["legs"]["stop"]["price"]
    u = f"{T}/{p['id']}"
    wider = client.patch(u, headers=h, json={"expected_version": 1, "stop_price": stop * 0.95})
    assert wider.status_code == 409 and "increases risk" in wider.text
    ok = client.patch(u, headers=h, json={"expected_version": 1, "stop_price": stop * 0.95,
                                          "confirm_risk_increase": True, "note": "earnings"})
    assert ok.status_code == 200 and ok.json()["version"] == 2
    stale = client.patch(u, headers=h, json={"expected_version": 1, "stop_price": stop})
    assert stale.status_code == 409 and "version" in stale.text
    act = client.post(f"{u}/activate", headers=h, json={}).json()
    assert act["status"] == "ACTIVE"
    assert client.post(f"{u}/pause", headers=h, json={}).status_code == 409
    paused = client.post(f"{u}/pause", headers=h, json={"confirm_risk_increase": True}).json()
    assert paused["price_levels"]["stop_loss"]["state"] == "PAUSED"
    res = client.post(f"{u}/resume", headers=h, json={}).json()
    assert res["status"] == "ACTIVE"
    assert client.post(f"{u}/cancel", headers=h, json={}).status_code == 409
    assert client.post(f"{u}/cancel", headers=h, json={"confirm_risk_increase": True}
                       ).json()["status"] == "CANCELLED"


def test_short_plan_cannot_book_and_isolation(client, user, paper):
    h = user["headers"]
    r = client.post(T, headers=h, json={"symbol": "TCS", "side": "SHORT", "book_entry": True,
                                        "portfolio_id": paper, "quantity": 5})
    assert r.status_code == 422
    p = client.post(T, headers=h, json={"symbol": "TCS"}).json()
    other = register(client)["headers"]
    assert client.get(f"{T}/{p['id']}", headers=other).status_code == 404
    assert client.post(f"{T}/{p['id']}/cancel", headers=other, json={}).status_code == 404
    assert client.get(T, headers=other).json() == []

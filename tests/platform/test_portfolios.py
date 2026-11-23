"""Portfolios on real lake data: ledger, corporate actions, valuation, risk, review."""

from __future__ import annotations

import pytest

from quantera.workers.runner import run_until_empty
from tests.platform.conftest import register

P = "/api/v1/portfolios"


@pytest.fixture
def book(client, user):
    h = user["headers"]
    pf = client.post(P, headers=h, json={"name": "Core", "max_position_weight": 0.25}).json()
    r = client.post(f"{P}/{pf['id']}/transactions", headers=h, json=[
        {"trade_date": "2024-01-02", "type": "DEPOSIT", "amount": 2_000_000},
        {"trade_date": "2024-01-03", "type": "BUY", "symbol": "RELIANCE", "quantity": 100,
         "price": 2600, "fees": 20},
        {"trade_date": "2024-01-03", "type": "BUY", "symbol": "TCS", "quantity": 50,
         "price": 3700},
        {"trade_date": "2024-01-03", "type": "BUY", "symbol": "INFY", "quantity": 100,
         "price": 1500},
        {"trade_date": "2024-01-03", "type": "BUY", "symbol": "HDFCBANK", "quantity": 100,
         "price": 1650}])
    assert r.status_code == 201, r.text
    return {"h": h, "id": pf["id"]}


def test_bonus_doubles_quantity_and_valuation(client, book):
    h = client.get(f"{P}/{book['id']}/holdings", headers=book["h"]).json()
    rel = next(x for x in h["positions"] if x["symbol"] == "RELIANCE")
    assert rel["quantity"] == 200  # 1:1 bonus, ex-date 2024-10-28
    assert rel["cost"] == pytest.approx(100 * 2600 + 20)  # cost unchanged
    assert rel["price"] > 0 and rel["market_value"] == pytest.approx(200 * rel["price"])
    assert h["cash"] == pytest.approx(2_000_000 - 260_020 - 185_000 - 150_000 - 165_000)
    assert h["nav"] == pytest.approx(h["cash"] + h["invested"])
    assert sum(x["weight"] for x in h["positions"]) == pytest.approx(h["invested"] / h["nav"])


def test_ledger_rules_void_and_idempotency(client, book):
    h, pid = book["h"], book["id"]
    over = client.post(f"{P}/{pid}/transactions", headers=h, json=[
        {"trade_date": "2024-02-01", "type": "SELL", "symbol": "TCS", "quantity": 60,
         "price": 3900}])
    assert over.status_code == 400 and "exceeds" in over.json()["error"]["message"]
    ok = client.post(f"{P}/{pid}/transactions", headers=h, json=[
        {"trade_date": "2024-02-01", "type": "SELL", "symbol": "TCS", "quantity": 10,
         "price": 3900, "external_ref": "sell-1"}]).json()
    again = client.post(f"{P}/{pid}/transactions", headers=h, json=[
        {"trade_date": "2024-02-01", "type": "SELL", "symbol": "TCS", "quantity": 10,
         "price": 3900, "external_ref": "sell-1"}]).json()
    assert len(ok) == 1 and again == []  # re-send is a no-op
    txs = client.get(f"{P}/{pid}/transactions", headers=h).json()
    buy_tcs = next(t for t in txs if t["symbol"] == "TCS" and t["type"] == "BUY")
    bad = client.post(f"{P}/{pid}/transactions/{buy_tcs['id']}/void", headers=h,
                      json={"reason": "typo"})
    assert bad.status_code == 400  # the later sell depends on this buy
    v = client.post(f"{P}/{pid}/transactions/{ok[0]['id']}/void", headers=h,
                    json={"reason": "entered twice"})
    assert v.status_code == 200 and v.json()["voided"]
    assert len(client.get(f"{P}/{pid}/transactions", headers=h).json()) == len(txs)  # kept
    bad_in = client.post(f"{P}/{pid}/transactions", headers=h, json=[
        {"trade_date": "2024-02-01", "type": "BUY", "symbol": "TCS", "quantity": 0, "price": 1}])
    assert bad_in.status_code == 422


def test_csv_holdings_import(client, user):
    h = user["headers"]
    pid = client.post(P, headers=h, json={"name": "Imported"}).json()["id"]
    good = "symbol,quantity,average_cost\nTCS,10,3500\nINFY,20,1400\n"
    r = client.post(f"{P}/{pid}/holdings/import", headers=h, data={"trade_date": "2024-06-03"},
                    files={"file": ("h.csv", good.encode(), "text/csv")})
    assert r.status_code == 201 and len(r.json()) == 2
    bad = "symbol,quantity,average_cost\n=HYPERLINK(1),10,1\n"
    r2 = client.post(f"{P}/{pid}/holdings/import", headers=h, data={"trade_date": "2024-06-03"},
                     files={"file": ("h.csv", bad.encode(), "text/csv")})
    assert r2.status_code == 400 and "formula" in str(r2.json())
    pos = client.get(f"{P}/{pid}/holdings", headers=h).json()["positions"]
    assert {x["symbol"] for x in pos} == {"TCS", "INFY"}


def test_risk_report(client, book):
    r = client.get(f"{P}/{book['id']}/risk", headers=book["h"]).json()
    assert r["status"] == "OK" and r["window_days"] > 200
    assert sum(r["risk_contribution"].values()) == pytest.approx(1.0)
    assert 0 < r["volatility"] < 1 and r["beta"] is not None
    assert r["concentration"]["effective_names"] > 1
    assert any("sector" in s["scenario"] for s in r["stress"])
    assert r["liquidity"]["max_days"] is not None


def test_review_scan_assigns_holding_states(client, book, lake):
    h, pid = book["h"], book["id"]
    before = client.get(f"{P}/{pid}/review", headers=h).json()
    assert {x["holding_state"] for x in before["rows"]} == {"INSUFFICIENT DATA"}
    job = client.post(f"{P}/{pid}/review/scan", headers=h, params={"reference_index": "NIFTY 50"})
    assert job.status_code == 202, job.text
    run_until_empty(client.app.state.db.sessions, lake)
    res = client.get(job.json()["results_url"], headers=h, params={"limit": 200}).json()["data"]
    held = {x["symbol"]: x for x in res if x["holding_state"]}
    assert set(held) == {"RELIANCE", "TCS", "INFY", "HDFCBANK"}
    assert len(res) >= 50  # ranked against the reference index
    after = client.get(f"{P}/{pid}/review", headers=h).json()
    states = {x["holding_state"] for x in after["rows"]}
    assert states <= {"HOLD", "ADD REVIEW", "TRIM REVIEW", "SELL REVIEW", "RISK ALERT"}


def test_isolation(client, book):
    other = register(client)["headers"]
    pid = book["id"]
    for path in (f"{P}/{pid}", f"{P}/{pid}/holdings", f"{P}/{pid}/transactions",
                 f"{P}/{pid}/risk", f"{P}/{pid}/review"):
        assert client.get(path, headers=other).status_code == 404, path
    assert client.post(f"{P}/{pid}/transactions", headers=other, json=[
        {"trade_date": "2024-01-02", "type": "DEPOSIT", "amount": 1}]).status_code == 404
    assert client.get(P, headers=other).json() == []

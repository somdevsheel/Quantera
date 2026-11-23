"""Paper trading on real lake prices (historical replay)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.platform.conftest import register

P = "/api/v1/paper-portfolios"


@pytest.fixture
def pf(client, user):
    h = user["headers"]
    pid = client.post("/api/v1/portfolios", headers=h, json={"name": "Paper", "is_paper": True}
                      ).json()["id"]
    client.post(f"/api/v1/portfolios/{pid}/transactions", headers=h, json=[
        {"trade_date": "2024-12-02", "type": "DEPOSIT", "amount": 1_000_000}])
    return pid


def order(client, h, pid, **kw):
    body = {"client_order_id": uuid.uuid4().hex[:12], "symbol": "TCS", "side": "BUY",
            "quantity": 10, "placed_on": "2025-01-02"} | kw
    r = client.post(f"{P}/{pid}/orders", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_market_order_fills_next_open_and_reconciles(client, user, pf):
    h = user["headers"]
    o = order(client, h, pf, client_order_id="m1")
    assert o["status"] == "WORKING" and o["venue"] == "PAPER (simulated)"
    assert client.post(f"{P}/{pf}/simulate", headers=h).json()["fills"] == 1
    done = client.get(f"{P}/{pf}/orders/{o['id']}", headers=h).json()
    assert done["status"] == "FILLED" and done["filled_quantity"] == 10 and done["fees"] > 0
    assert done["simulated_through"] > "2025-01-02"  # never the placement day itself
    assert [e["to"] for e in done["events"] if e["to"]] == ["WORKING", "FILLED"]
    tx = [t for t in client.get(f"/api/v1/portfolios/{pf}/transactions", headers=h).json()
          if t["source"] == "paper"]
    assert len(tx) == 1 and tx[0]["type"] == "BUY" and tx[0]["quantity"] == 10
    assert client.get(f"{P}/{pf}/reconcile", headers=h).json()["status"] == "RECONCILED"
    again = order(client, h, pf, client_order_id="m1")
    assert again["id"] == o["id"]  # idempotent


def test_pre_trade_rejections(client, user, pf):
    h = user["headers"]
    short = order(client, h, pf, side="SELL", quantity=5)
    assert short["status"] == "REJECTED" and "shorting" in short["reason"]
    big = order(client, h, pf, quantity=100_000)
    assert big["status"] == "REJECTED" and "INR" in big["reason"]
    real = client.post("/api/v1/portfolios", headers=h, json={"name": "Real"}).json()["id"]
    r = client.post(f"{P}/{real}/orders", headers=h, json={
        "client_order_id": "x", "symbol": "TCS", "side": "BUY", "quantity": 1})
    assert r.status_code == 400 and "PAPER" in r.text


def test_limit_day_expiry_gtc_cancel(client, user, pf):
    h = user["headers"]
    day = order(client, h, pf, order_type="LIMIT", limit_price=1.0, tif="DAY")
    gtc = order(client, h, pf, order_type="LIMIT", limit_price=1.0, tif="GTC")
    client.post(f"{P}/{pf}/simulate", headers=h)
    assert client.get(f"{P}/{pf}/orders/{day['id']}", headers=h).json()["status"] == "EXPIRED"
    st = client.get(f"{P}/{pf}/orders/{gtc['id']}", headers=h).json()["status"]
    assert st in ("WORKING", "EXPIRED")  # GTC lasts 90 sessions
    if st == "WORKING":
        c = client.post(f"{P}/{pf}/orders/{gtc['id']}/cancel", headers=h).json()
        assert c["status"] == "CANCELLED"
        assert client.post(f"{P}/{pf}/orders/{gtc['id']}/cancel", headers=h).status_code == 409


def test_risk_off_blocks_buys_until_reset(client, user, pf, settings):
    h = user["headers"]
    client.put(f"{P}/{pf}/risk-policy", headers=h, json={"max_drawdown": 0.10})
    first = client.post(f"{P}/{pf}/risk-policy/check", headers=h).json()
    assert first["state"] == "NORMAL"
    from sqlalchemy import create_engine

    eng = create_engine(settings.database_url)
    with eng.begin() as c:  # simulate an earlier, higher NAV peak
        c.execute(text(f'SET search_path TO "{settings.db_schema}"'))
        c.execute(text("UPDATE risk_policies SET nav_peak = nav_peak * 2"))
    eng.dispose()
    chk = client.post(f"{P}/{pf}/risk-policy/check", headers=h).json()
    assert chk["state"] == "RISK_OFF" and chk["breaches"][0]["kind"] == "DRAWDOWN"
    blocked = order(client, h, pf)
    assert blocked["status"] == "REJECTED" and "RISK_OFF" in blocked["reason"]
    assert client.post(f"{P}/{pf}/risk-policy/reset", headers=h, json={"note": "x"}
                       ).status_code == 422
    assert client.post(f"{P}/{pf}/risk-policy/reset", headers=h,
                       json={"note": "reviewed with PM"}).json()["state"] == "NORMAL"


def test_isolation(client, user, pf):
    h = user["headers"]
    o = order(client, h, pf)
    other = register(client)["headers"]
    assert client.get(f"{P}/{pf}/orders", headers=other).status_code == 404
    assert client.post(f"{P}/{pf}/orders/{o['id']}/cancel", headers=other).status_code == 404
    assert client.post(f"{P}/{pf}/simulate", headers=other).status_code == 404

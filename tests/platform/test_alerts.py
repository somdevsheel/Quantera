"""Alerts on real data: rules fire once per bar, cooldown, feed, acknowledgement, isolation."""

from __future__ import annotations

from tests.platform.conftest import register

A = "/api/v1/alerts"


def test_rules_fire_once_per_bar_and_feed(client, user):
    h = user["headers"]
    always = client.post(f"{A}/rules", headers=h, json={
        "name": "TCS above 1", "kind": "PRICE_ABOVE", "symbol": "TCS", "threshold": 1.0})
    assert always.status_code == 201, always.text
    never = client.post(f"{A}/rules", headers=h, json={
        "name": "TCS below 1", "kind": "PRICE_BELOW", "symbol": "TCS", "threshold": 1.0}).json()
    client.post(f"{A}/rules", headers=h, json={"name": "rsi", "kind": "RSI_ABOVE",
                                              "symbol": "INFY", "threshold": 0})
    first = client.post(f"{A}/evaluate", headers=h).json()
    assert first == {"rules": 3, "triggered": 2}
    assert client.post(f"{A}/evaluate", headers=h).json()["triggered"] == 0  # deduplicated
    feed = client.get(f"{A}/feed", headers=h).json()
    assert {x["kind"] for x in feed} == {"PRICE_ABOVE", "RSI_ABOVE"}
    assert all(x["route"].startswith("/stocks/") for x in feed)
    ack = client.post(f"{A}/{feed[0]['id']}/acknowledge", headers=h)
    assert ack.status_code == 200
    assert len(client.get(f"{A}/feed", headers=h, params={"unacknowledged": True}).json()) == 1
    assert client.delete(f"{A}/rules/{never['id']}", headers=h).status_code == 204


def test_validation_and_isolation(client, user):
    h = user["headers"]
    assert client.post(f"{A}/rules", headers=h, json={"name": "x", "kind": "MAGIC",
                                                      "symbol": "TCS"}).status_code == 422
    assert client.post(f"{A}/rules", headers=h, json={"name": "x", "kind": "PRICE_ABOVE",
                                                      "symbol": "NOSUCHX"}).status_code == 404
    r = client.post(f"{A}/rules", headers=h, json={"name": "x", "kind": "MOVE_1D",
                                                   "symbol": "TCS", "threshold": 0.0}).json()
    other = register(client)["headers"]
    assert client.delete(f"{A}/rules/{r['id']}", headers=other).status_code == 404
    assert client.get(f"{A}/rules", headers=other).json() == []

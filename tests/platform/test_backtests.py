"""Strategy library and backtests on real lake data (point-in-time liquid universe)."""

from __future__ import annotations

from quantera.workers.runner import run_until_empty

B = "/api/v1/backtests"


def run_bt(client, h, lake, **body):
    r = client.post(B, headers=h, json=body)
    assert r.status_code == 202, r.text
    run_until_empty(client.app.state.db.sessions, lake)
    out = client.get(f"{B}/{r.json()['id']}", headers=h).json()
    assert out["status"] == "SUCCEEDED", out["error"]
    return out


def test_library_is_honest(client, user):
    d = client.get("/api/v1/strategies", headers=user["headers"]).json()
    fams = d["families"]
    assert len(fams) == 20 and len({f["code"] for f in fams}) == 20
    assert not any(f["status"] in ("BACKTEST_PASSED", "PAPER_TRADING", "APPROVED") for f in fams)
    for f in fams:
        if f["status"] == "DATA_BLOCKED":
            assert f["blocked_by"], f["code"]
        if f["status"] == "RESEARCH":  # a real implementation: backtest signals or the registry
            assert f["implementation"], f["code"]
            assert f["signals"] or "/models" in f["implementation"], f["code"]
    assert next(f for f in fams if f["code"] == "DIGITAL_ASSETS")["status"] == "DISABLED"
    assert client.get("/api/v1/strategies/value", headers=user["headers"]).json()["code"] == "VALUE"
    assert client.get("/api/v1/strategies/NOPE", headers=user["headers"]).status_code == 404


def test_momentum_backtest_walk_forward_and_trials(client, user, lake):
    h = user["headers"]
    out = run_bt(client, h, lake, signal="momentum_12_1", start="2022-01-01", end="2024-12-31",
                 liquid_top=30, grid=[{"lookback": 126}, {"lookback": 252}], train_days=252,
                 test_days=126)
    s = out["summary"]
    m = s["result"]["metrics"]
    assert s["family"] == "MOMENTUM_TREND" and s["warnings"] == []
    assert m["status"] == "OK" and m["rebalances"] >= 30 and m["total_costs"] > 0
    assert set(s["result"]["costs"]) >= {"stt", "stamp", "gst", "impact"}
    eq = s["result"]["equity"]
    assert eq[0]["date"] >= "2022-01-01" and eq[-1]["date"] <= "2024-12-31"
    assert s["universe"]["instruments_over_window"] >= 30  # membership changes over time
    wf = s["walk_forward"]
    assert wf["folds"] and wf["out_of_sample"]["status"] == "OK"
    assert s["deflated_sharpe"]["trials"] == 3
    again = run_bt(client, h, lake, signal="trend_sma200", start="2023-01-01", end="2024-06-30",
                   liquid_top=20)
    assert again["summary"]["deflated_sharpe"]["trials"] == 2  # the earlier momentum run counts
    reg = client.get(B, headers=h).json()
    assert len(reg) == 2 and {r["family"] for r in reg} == {"MOMENTUM_TREND"}


def test_value_backtest_uses_point_in_time_filings(client, user, lake):
    out = run_bt(client, user["headers"], lake, signal="value_earnings_yield",
                 start="2023-06-01", end="2024-06-30", liquid_top=20, selection="TOP_N", top_n=5)
    assert out["summary"]["result"]["metrics"]["status"] == "OK"
    assert out["summary"]["result"]["trade_count"] > 0


def test_snapshot_universe_carries_survivorship_warning(client, user, lake):
    h = user["headers"]
    u = client.post("/api/v1/universes", headers=h, json={
        "name": "picks", "kind": "MANUAL",
        "symbols": ["TCS", "INFY", "RELIANCE", "HDFCBANK", "ITC", "SBIN", "LT", "ICICIBANK"]}
    ).json()
    snap = client.post(f"/api/v1/universes/{u['id']}/snapshots", headers=h, json={}).json()
    out = run_bt(client, h, lake, signal="reversal_1m", start="2023-01-01", end="2024-06-30",
                 universe="SNAPSHOT", universe_snapshot_id=snap["id"], selection="TOP_N", top_n=3)
    assert any("SURVIVORSHIP" in w for w in out["summary"]["warnings"])


def test_validation(client, user):
    h = user["headers"]
    assert client.post(B, headers=h, json={"signal": "magic", "start": "2023-01-01",
                                           "end": "2024-01-01"}).status_code == 422
    assert client.post(B, headers=h, json={"signal": "momentum_12_1", "start": "2023-01-01",
                                           "end": "2023-02-01"}).status_code == 422
    assert client.get(B).status_code == 401

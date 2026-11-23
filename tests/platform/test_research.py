"""Stock research page on real lake data."""

from __future__ import annotations

from quantera.workers.runner import run_until_empty

S = "/api/v1/stocks"
SECTIONS = {"overview", "technical", "volatility", "quant", "fundamentals", "valuation",
            "events", "risk", "forecast", "options"}


def test_research_page_sections_and_statuses(client, user):
    r = client.get(f"{S}/NSE/tcs", headers=user["headers"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert SECTIONS <= set(d) and d["instrument"]["route"] == "/stocks/XNSE/TCS"
    assert d["lake_release"].startswith("lake-") and d["overview"]["close"] > 0
    assert d["overview"]["freshness"]["prices_last"] <= d["as_of"]
    t = d["technical"]
    assert len(t["bars"]) == len(t["indicators"]["rsi14"]) == 260
    assert 0 <= t["signals"]["rsi14"] <= 100 and t["signals"]["support_20d"] > 0
    est = d["volatility"]["estimators"]["3m"]
    assert all(est[k] > 0 for k in ("parkinson", "garman_klass", "rogers_satchell", "yang_zhang"))
    assert d["quant"]["status"] == "OK" and "beta" in d["quant"]
    assert d["fundamentals"]["status"] in ("OK", "STALE") and d["fundamentals"]["quarters"]
    assert d["fundamentals"]["quarters"][0]["consolidated"] == "Consolidated"
    assert "dcf" in d["valuation"]["unsupported"]
    assert d["forecast"]["status"] == "MODEL UNAVAILABLE"
    assert d["options"]["status"] == "UNSUPPORTED"
    assert d["overview"]["decision"] is None  # no scan yet in this workspace


def test_research_shows_latest_scan_decision(client, user, lake):
    h = user["headers"]
    u = client.post("/api/v1/universes", headers=h, json={"name": "x", "kind": "MANUAL",
                    "symbols": ["TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "LTIM"]}).json()
    client.post("/api/v1/analysis/jobs", headers=h, json={"universe_id": u["id"]})
    run_until_empty(client.app.state.db.sessions, lake)
    dec = client.get(f"{S}/NSE/TCS", headers=h).json()["overview"]["decision"]
    assert dec and dec["state"] and dec["job_id"]


def test_historical_as_of_is_point_in_time(client, user):
    d = client.get(f"{S}/NSE/TCS", headers=user["headers"], params={"as_of": "2023-03-31"}).json()
    assert d["as_of"] == "2023-03-31" and d["overview"]["last_date"] <= "2023-03-31"
    assert d["technical"]["bars"][-1]["date"] <= "2023-03-31"
    filed = d["fundamentals"]["latest_filed"]
    assert filed is None or filed[:10] <= "2023-03-31"


def test_search_and_errors(client, user):
    h = user["headers"]
    hits = client.get(f"{S}/search", headers=h, params={"q": "INFY"}).json()
    assert any(x["listing_symbol"] == "INFY" and x["route"] == "/stocks/XNSE/INFY" for x in hits)
    assert client.get(f"{S}/NSE/NOSUCHSYMBOLX", headers=h).status_code == 404
    assert client.get(f"{S}/LSE/TCS", headers=h).status_code == 400
    assert client.get(f"{S}/NSE/TCS").status_code == 401


def test_bse_listing_with_two_scrip_codes(client, user):
    """On BSE one ISIN can carry several scrip codes (TCS: 532540 and 132540); the research
    page must use the exact listing instead of failing as ambiguous."""
    h = user["headers"]
    r = client.get("/api/v1/stocks/XBOM/TCS", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["instrument"]["exchange"] == "XBOM" and r.json()["overview"]["close"]
    hits = client.get("/api/v1/stocks/search", headers=h, params={"q": "TCS"}).json()
    mics = [x["exchange_mic"] for x in hits]
    assert mics == sorted(mics, key=lambda m: m != "XNSE")  # NSE first

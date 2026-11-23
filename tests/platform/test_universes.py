"""Universes and watchlists against the real lake release (no mocks)."""

from __future__ import annotations

from tests.platform.conftest import register

U, W = "/api/v1/universes", "/api/v1/watchlists"


def make(client, h, **body):
    r = client.post(U, headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def snap(client, h, uid, **body):
    r = client.post(f"{U}/{uid}/snapshots", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_catalog_lists_lake_indices_and_industries(client, user):
    c = client.get(f"{U}/catalog", headers=user["headers"]).json()
    names = {i["index"] for i in c["indices"]}
    assert {"NIFTY 50", "NIFTY 500"} <= names and c["industries"]
    assert any("survivorship" in n for n in c["notes"])


def test_index_universe_snapshot_is_reproducible(client, user):
    h = user["headers"]
    u = make(client, h, name="Nifty 50", kind="INDEX", index="NIFTY 50")
    s1 = snap(client, h, u["id"])
    assert s1["member_count"] == 50 and s1["lake_release"].startswith("lake-")
    assert s1["source_snapshot_date"] and len(s1["content_hash"]) == 64
    assert all(m["instrument_id"].startswith("IN:") and m["industry"] for m in s1["members"])
    s2 = snap(client, h, u["id"])
    assert s2["id"] != s1["id"] and s2["content_hash"] == s1["content_hash"]  # same inputs
    listed = client.get(f"{U}/{u['id']}/snapshots", headers=h).json()
    assert [s["id"] for s in listed] == [s2["id"], s1["id"]] and listed[0]["members"] is None
    full = client.get(f"/api/v1/universe-snapshots/{s1['id']}", headers=h).json()
    assert len(full["members"]) == 50


def test_index_before_first_snapshot_is_refused_not_backfilled(client, user):
    h = user["headers"]
    u = make(client, h, name="Old Nifty", kind="INDEX", index="NIFTY 50")
    r = client.post(f"{U}/{u['id']}/snapshots", headers=h, json={"as_of": "2020-01-01"})
    assert r.status_code == 404 and "survivorship" in r.json()["error"]["message"]


def test_sector_universe_and_no_historical_as_of(client, user):
    h = user["headers"]
    inds = client.get(f"{U}/catalog", headers=h).json()["industries"]
    it = next(i for i in inds if "Information Technology" in i["industry"])
    u = make(client, h, name="IT", kind="SECTOR", industry=it["industry"])
    s = snap(client, h, u["id"])
    assert s["member_count"] == it["members"]
    assert any(m["symbol"] == "TCS" for m in s["members"])
    r = client.post(f"{U}/{u['id']}/snapshots", headers=h, json={"as_of": "2024-01-01"})
    assert r.status_code == 400 and "look-ahead" in r.json()["error"]["message"]


def test_manual_universe_resolves_and_reports_unresolved(client, user):
    h = user["headers"]
    u = make(client, h, name="Picks", kind="MANUAL",
             symbols=["tcs", "INFY", "RELIANCE", "TCS", "NOSUCHSYMBOLX"])
    s = snap(client, h, u["id"])
    assert sorted(m["symbol"] for m in s["members"]) == ["INFY", "RELIANCE", "TCS"]
    assert [x["input"] for x in s["unresolved"]] == ["NOSUCHSYMBOLX"]


def test_csv_import_rejects_formula_cells(client, user):
    h = user["headers"]
    csv_text = "Symbol,Note\nTCS,core\n=HYPERLINK(\"x\"),bad\n+1+2,bad\nINFY,\n,\n@SUM(A1),bad\n"
    r = client.post(f"{U}/import-csv", headers=h, data={"name": "From CSV"},
                    files={"file": ("list.csv", csv_text.encode(), "text/csv")})
    assert r.status_code == 201, r.text
    u = r.json()
    assert u["params"]["source"] == "csv" and u["params"]["symbols"] == ["TCS", "INFY"]
    assert len(u["params"]["rejected"]) == 3
    assert all("formula" in x["reason"] for x in u["params"]["rejected"])
    s = snap(client, h, u["id"])
    assert s["member_count"] == 2 and len(s["unresolved"]) == 3
    empty = client.post(f"{U}/import-csv", headers=h, data={"name": "x"},
                        files={"file": ("e.csv", b"symbol\n=1\n", "text/csv")})
    assert empty.status_code == 400


def test_watchlist_items_and_watchlist_universe(client, user):
    h = user["headers"]
    w = client.post(W, headers=h, json={"name": "Core"}).json()
    assert client.post(W, headers=h, json={"name": "Core"}).status_code == 409
    a = client.post(f"{W}/{w['id']}/items", headers=h, json={"symbol": "tcs"})
    assert a.status_code == 201 and a.json()["instrument_id"].startswith("IN:")
    assert client.post(f"{W}/{w['id']}/items", headers=h, json={"symbol": "TCS"}
                       ).status_code == 409
    assert client.post(f"{W}/{w['id']}/items", headers=h, json={"symbol": "NOSUCHSYMBOLX"}
                       ).status_code == 404
    client.post(f"{W}/{w['id']}/items", headers=h, json={"symbol": "INFY"})
    u = make(client, h, name="WL", kind="WATCHLIST", watchlist_id=w["id"])
    s = snap(client, h, u["id"])
    assert sorted(m["symbol"] for m in s["members"]) == ["INFY", "TCS"]
    item = client.get(f"{W}/{w['id']}", headers=h).json()["items"][0]
    assert client.delete(f"{W}/{w['id']}/items/{item['id']}", headers=h).status_code == 204
    assert len(client.get(f"{W}/{w['id']}", headers=h).json()["items"]) == 1


def test_validation(client, user):
    h = user["headers"]
    assert client.post(U, headers=h, json={"name": "x", "kind": "INDEX"}).status_code == 422
    assert client.post(U, headers=h, json={"name": "x", "kind": "HOLDINGS"}).status_code == 422
    assert client.post(U, headers=h, json={"name": "x", "kind": "MANUAL", "symbols": ["A"],
                                           "exchange": "LSE"}).status_code == 422


def test_workspace_isolation_idor(client, user):
    h = user["headers"]
    u = make(client, h, name="Mine", kind="INDEX", index="NIFTY 50")
    s = snap(client, h, u["id"])
    w = client.post(W, headers=h, json={"name": "Private"}).json()
    other = register(client)["headers"]
    for path in (f"{U}/{u['id']}", f"/api/v1/universe-snapshots/{s['id']}", f"{W}/{w['id']}",
                 f"{U}/{u['id']}/snapshots"):
        assert client.get(path, headers=other).status_code == 404, path
    assert client.post(f"{U}/{u['id']}/snapshots", headers=other, json={}).status_code == 404
    assert client.delete(f"{U}/{u['id']}", headers=other).status_code == 404
    assert client.post(f"{W}/{w['id']}/items", headers=other, json={"symbol": "TCS"}
                       ).status_code == 404
    assert client.post(U, headers=other, json={"name": "x", "kind": "WATCHLIST",
                                               "watchlist_id": w["id"]}).status_code == 404
    assert client.get(U, headers=other).json() == [] and client.get(W, headers=other).json() == []
    assert client.get(U).status_code == 401

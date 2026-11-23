"""Batch scan jobs end to end: 202 + job id, worker, decision records, filters, export,
idempotency, cancellation, quotas, isolation. Real lake data, real PostgreSQL."""

from __future__ import annotations

import csv
import io

import pytest

from quantera.workers.runner import run_until_empty
from tests.platform.conftest import register

J = "/api/v1/analysis/jobs"


def universe(client, h, **body):
    r = client.post("/api/v1/universes", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def work(client, lake):
    return run_until_empty(client.app.state.db.sessions, lake)


@pytest.fixture
def nifty(client, user):
    return universe(client, user["headers"], name="N50", kind="INDEX", index="NIFTY 50")


def test_scan_end_to_end_on_nifty50(client, user, lake, nifty):
    h = user["headers"]
    r = client.post(J, headers=h, json={"universe_id": nifty})
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["status"] == "QUEUED" and r.headers["Location"] == job["status_url"]
    assert job["params"]["notes"] and "after as_of" in job["params"]["notes"][0]
    assert work(client, lake) == 1
    done = client.get(job["status_url"], headers=h).json()
    assert done["status"] in ("SUCCEEDED", "PARTIAL"), done["error"]
    assert done["progress"] == 1.0 and done["done_items"] == 50
    assert done["versions"]["policy"] == "decision_policy_v1"
    assert done["versions"]["lake_release"].startswith("lake-")
    assert sum(done["summary"]["states"].values()) == 50
    res = client.get(job["results_url"], headers=h, params={"limit": 200}).json()
    assert res["total"] == 50 and [x["rank"] for x in res["data"]] == list(range(1, 51))
    for x in res["data"]:
        assert x["route"].startswith("/stocks/XNSE/") and x["as_of"] == job["as_of"]
        assert set(x["pillars"]) == {"MOMENTUM", "QUALITY", "GROWTH", "VALUATION", "RISK"}
        if x["state"] == "BUY CANDIDATE":  # needs fresh, independent fundamental evidence
            assert any(x["pillars"][p] is not None for p in ("QUALITY", "GROWTH", "VALUATION"))
    one = client.get(f"{J}/{job['id']}/results/TCS", headers=h).json()
    assert one["symbol"] == "TCS" and one["features"]["HISTORY_DAYS"] > 250
    assert one["versions"]["universe_hash"] and "not investment advice" in one["disclaimer"]


def test_results_filters_sort_paging_and_export(client, user, lake, nifty):
    h = user["headers"]
    job = client.post(J, headers=h, json={"universe_id": nifty}).json()
    work(client, lake)
    base = f"{J}/{job['id']}/results"
    full = client.get(base, headers=h, params={"limit": 200}).json()["data"]
    states = {x["state"] for x in full}
    st = sorted(states)[0]
    only = client.get(base, headers=h, params={"state": st, "limit": 200}).json()
    assert {x["state"] for x in only["data"]} == {st}
    by_opp = client.get(base, headers=h, params={"sort": "opportunity", "order": "desc",
                                                 "limit": 200}).json()["data"]
    opps = [x["opportunity"] for x in by_opp if x["opportunity"] is not None]
    assert opps == sorted(opps, reverse=True)
    p1 = client.get(base, headers=h, params={"limit": 20}).json()
    p2 = client.get(base, headers=h, params={"limit": 20, "cursor": p1["next_cursor"]}).json()
    assert [x["rank"] for x in p1["data"] + p2["data"]] == list(range(1, 41))
    assert client.get(base, headers=h, params={"state": "BOGUS"}).status_code == 400
    exp = client.get(f"{J}/{job['id']}/export.csv", headers=h)
    rows = list(csv.reader(io.StringIO(exp.text)))
    assert rows[0][:3] == ["rank", "symbol", "name"] and len(rows) == 51
    assert not any(c.startswith(("=", "+", "@")) for r in rows for c in r)  # formula-safe


def test_idempotency_quota_cancel_and_retry(client, user, lake, nifty):
    h = user["headers"] | {"Idempotency-Key": "scan-123"}
    a = client.post(J, headers=h, json={"universe_id": nifty})
    snap_id = a.json()["universe_snapshot_id"]
    b = client.post(J, headers=h, json={"universe_snapshot_id": snap_id})
    assert a.status_code == 202 and b.status_code == 200  # same request: same job back
    assert b.json()["id"] == a.json()["id"]
    c = client.post(J, headers=h, json={"universe_id": nifty})  # resolves a NEW snapshot
    assert c.status_code == 409  # same key, different request
    plain = user["headers"]
    ids = [client.post(J, headers=plain, json={"universe_snapshot_id": snap_id}).json()["id"]
           for _ in range(2)]
    over = client.post(J, headers=plain, json={"universe_snapshot_id": snap_id})
    assert over.status_code == 429  # 3 open jobs per workspace
    cancelled = client.post(f"{J}/{ids[0]}/cancel", headers=plain).json()
    assert cancelled["status"] == "CANCELLED"
    retried = client.post(f"{J}/{ids[0]}/retry", headers=plain)
    assert retried.status_code == 202 and retried.json()["summary"]["retry_of"] == ids[0]
    work(client, lake)
    assert client.get(f"{J}/{ids[0]}", headers=plain).json()["status"] == "CANCELLED"


def test_point_in_time_guards(client, user, lake, nifty):
    h = user["headers"]
    old = client.post(J, headers=h, json={"universe_id": nifty, "as_of": "2025-06-30"})
    assert old.status_code == 400 and "survivorship" in old.json()["error"]["message"]
    manual = universe(client, h, name="Hist", kind="MANUAL",
                      symbols=["TCS", "INFY", "RELIANCE", "HDFCBANK", "ITC", "SBIN"])
    r = client.post(J, headers=h, json={"universe_id": manual, "as_of": "2024-06-28"})
    assert r.status_code == 202
    work(client, lake)
    recs = client.get(f"{J}/{r.json()['id']}/results", headers=h).json()["data"]
    for x in recs:
        assert x["data_as_of"]["prices_last"] <= "2024-06-28"
        filed = x["data_as_of"]["fundamentals"]["latest_filed"]
        assert filed is None or filed[:10] <= "2024-06-28"  # nothing filed after as_of


def test_isolation_and_auth(client, user, lake, nifty):
    h = user["headers"]
    job = client.post(J, headers=h, json={"universe_id": nifty}).json()
    other = register(client)["headers"]
    for path in (f"{J}/{job['id']}", f"{J}/{job['id']}/results", f"{J}/{job['id']}/export.csv"):
        assert client.get(path, headers=other).status_code == 404, path
    assert client.post(f"{J}/{job['id']}/cancel", headers=other).status_code == 404
    assert client.post(J, headers=other, json={"universe_id": nifty}).status_code == 404
    assert client.get(J).status_code == 401

"""Group workspaces, invitations, workspace switching and read-only share grants."""

from __future__ import annotations

from sqlalchemy import create_engine, text

from tests.platform.conftest import register


def _ws(client, h, name="Desk"):
    r = client.post("/api/v1/workspaces", headers=h, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _switch(client, h, wid):
    r = client.post("/api/v1/auth/switch-workspace", headers=h, json={"workspace_id": wid})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _watchlist(client, h):
    wl = client.post("/api/v1/watchlists", headers=h, json={"name": "Ideas"}).json()["id"]
    r = client.post(f"/api/v1/watchlists/{wl}/items", headers=h, json={"symbol": "TCS"})
    assert r.status_code == 201, r.text
    return wl


def test_invite_accept_switch_and_remove(client, user):
    h = user["headers"]
    wid = _ws(client, h)
    bob = register(client)
    inv = client.post(f"/api/v1/workspaces/{wid}/invites", headers=h,
                      json={"email": bob["email"], "role": "GUEST"}).json()
    # wrong person cannot use it
    eve = register(client)
    assert client.post("/api/v1/invites/accept", headers=eve["headers"],
                       json={"token": inv["token"]}).status_code == 403
    ok = client.post("/api/v1/invites/accept", headers=bob["headers"],
                     json={"token": inv["token"]})
    assert ok.status_code == 200 and ok.json()["role"] == "GUEST"
    assert client.post("/api/v1/invites/accept", headers=bob["headers"],
                       json={"token": inv["token"]}).status_code == 409  # single use
    names = {w["name"] for w in client.get("/api/v1/workspaces", headers=bob["headers"]).json()}
    assert "Desk" in names
    # a guest is read-only, and cannot invite
    bh = _switch(client, bob["headers"], wid)
    assert client.post("/api/v1/watchlists", headers=bh, json={"name": "x"}).status_code == 403
    assert client.post(f"/api/v1/workspaces/{wid}/invites", headers=bh,
                       json={"email": "z@example.com"}).status_code == 403
    # someone else cannot switch into it
    assert client.post("/api/v1/auth/switch-workspace", headers=eve["headers"],
                       json={"workspace_id": wid}).status_code == 404
    # owner removes bob; bob can no longer switch in
    bob_id = client.get("/api/v1/auth/me", headers=bob["headers"]).json()["id"]
    assert client.delete(f"/api/v1/workspaces/{wid}/members/{bob_id}",
                         headers=h).status_code == 204
    assert client.post("/api/v1/auth/switch-workspace", headers=bob["headers"],
                       json={"workspace_id": wid}).status_code == 404


def test_expired_and_revoked_invites(client, user, settings):
    h = user["headers"]
    wid = _ws(client, h)
    bob = register(client)
    a = client.post(f"/api/v1/workspaces/{wid}/invites", headers=h,
                    json={"email": bob["email"]}).json()
    b = client.post(f"/api/v1/workspaces/{wid}/invites", headers=h,
                    json={"email": bob["email"]}).json()
    assert client.delete(f"/api/v1/workspaces/{wid}/invites/{a['id']}",
                         headers=h).status_code == 204
    assert client.post("/api/v1/invites/accept", headers=bob["headers"],
                       json={"token": a["token"]}).status_code == 409
    eng = create_engine(settings.database_url)
    with eng.begin() as c:
        c.execute(text(f'SET search_path TO "{settings.db_schema}"'))
        c.execute(text("UPDATE invitations SET expires_at = now() - interval '1 day' "
                       "WHERE id = :i"), {"i": b["id"]})
    eng.dispose()
    r = client.post("/api/v1/invites/accept", headers=bob["headers"], json={"token": b["token"]})
    assert r.status_code == 409 and "expired" in r.text
    # personal workspaces are private
    personal = next(w["id"] for w in client.get("/api/v1/workspaces", headers=h).json()
                    if w["personal"])
    assert client.post(f"/api/v1/workspaces/{personal}/invites", headers=h,
                       json={"email": bob["email"]}).status_code == 400


def test_share_watchlist_read_only_and_revocable(client, user):
    h = user["headers"]
    wl = _watchlist(client, h)
    bob, eve = register(client), register(client)
    g = client.post("/api/v1/shares", headers=h, json={
        "resource_type": "WATCHLIST", "resource_id": wl, "grantee_email": bob["email"],
        "expires_in_days": 30})
    assert g.status_code == 201, g.text
    gid = g.json()["id"]
    mine = client.get("/api/v1/shared-with-me", headers=bob["headers"]).json()
    assert [s["id"] for s in mine] == [gid]
    got = client.get(f"/api/v1/shared/{gid}", headers=bob["headers"]).json()
    assert got["read_only"] and [i["symbol"] for i in got["items"]] == ["TCS"]
    # IDOR: the grant does not open the underlying resource, and others cannot use it
    assert client.get(f"/api/v1/watchlists/{wl}", headers=bob["headers"]).status_code == 404
    assert client.get(f"/api/v1/shared/{gid}", headers=eve["headers"]).status_code == 404
    assert client.delete(f"/api/v1/shares/{gid}", headers=bob["headers"]).status_code == 404
    # cannot share what you do not own
    assert client.post("/api/v1/shares", headers=eve["headers"], json={
        "resource_type": "WATCHLIST", "resource_id": wl,
        "grantee_email": eve["email"]}).status_code == 404
    # revoke: gone immediately
    assert client.delete(f"/api/v1/shares/{gid}", headers=h).json()["revoked"] is True
    assert client.get(f"/api/v1/shared/{gid}", headers=bob["headers"]).status_code == 404
    assert client.get("/api/v1/shared-with-me", headers=bob["headers"]).json() == []


def test_share_to_group_workspace(client, user):
    h = user["headers"]
    wid = _ws(client, h, "Team")
    bob = register(client)
    inv = client.post(f"/api/v1/workspaces/{wid}/invites", headers=h,
                      json={"email": bob["email"]}).json()
    client.post("/api/v1/invites/accept", headers=bob["headers"], json={"token": inv["token"]})
    wl = _watchlist(client, h)  # in alice's personal workspace
    g = client.post("/api/v1/shares", headers=h, json={
        "resource_type": "WATCHLIST", "resource_id": wl, "grantee_workspace_id": wid}).json()
    # visible to bob only while working in that workspace
    assert client.get("/api/v1/shared-with-me", headers=bob["headers"]).json() == []
    bh = _switch(client, bob["headers"], wid)
    assert [s["id"] for s in client.get("/api/v1/shared-with-me", headers=bh).json()] == [g["id"]]
    # cannot share into a workspace you do not belong to
    eve = register(client)
    wl2 = _watchlist(client, eve["headers"])
    assert client.post("/api/v1/shares", headers=eve["headers"], json={
        "resource_type": "WATCHLIST", "resource_id": wl2,
        "grantee_workspace_id": wid}).status_code == 404


def test_market_overview(client, user):
    r = client.get("/api/v1/market/overview", headers=user["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    nifty = next(i for i in body["indices"] if i["index_id"] == "NSEIDX:NIFTY_50")
    assert nifty["status"] == "OK" and nifty["as_of"] == body["latest_market_date"]
    assert nifty["low_52w"] <= nifty["close"] <= nifty["high_52w"] and len(nifty["spark"]) > 20
    assert body["freshness"] in ("FRESH", "STALE")
    assert client.get("/api/v1/market/overview").status_code == 401

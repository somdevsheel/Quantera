"""Identity: registration, login, token rotation and reuse detection, revocation, isolation."""

from __future__ import annotations

import uuid

from sqlalchemy import create_engine, text

from tests.platform.conftest import PASSWORD, register

A = "/api/v1/auth"


def test_health(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    r = client.get("/health/ready")
    assert r.status_code == 200 and r.json()["database"] == "up"
    assert r.headers["X-Request-ID"]


def test_register_creates_private_workspace_and_me(client, user):
    me = client.get(f"{A}/me", headers=user["headers"]).json()
    assert me["email"] == user["email"] and me["role"] == "OWNER"
    assert me["workspace"]["name"].endswith("workspace")
    assert "password" not in str(me).lower()


def test_register_rejects_duplicates_and_weak_passwords(client, user):
    dup = client.post(f"{A}/register", json={"email": user["email"].upper(), "password": PASSWORD,
                                             "full_name": "X"})
    assert dup.status_code == 409  # emails are case-insensitive
    weak = client.post(f"{A}/register", json={"email": "w@example.com", "password": "short",
                                              "full_name": "X"})
    assert weak.status_code == 400 and weak.json()["error"]["code"] == "bad_request"
    bad = client.post(f"{A}/register", json={"email": "not-an-email", "password": PASSWORD,
                                             "full_name": "X"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"


def test_login_success_and_generic_failure(client, user):
    ok = client.post(f"{A}/login", json={"email": user["email"], "password": PASSWORD})
    assert ok.status_code == 200 and ok.json()["token_type"] == "bearer"
    wrong = client.post(f"{A}/login", json={"email": user["email"], "password": "nope-nope-nope"})
    unknown = client.post(f"{A}/login", json={"email": "ghost@example.com", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]  # no enumeration


def test_refresh_rotates_and_reuse_revokes_everything(client, user):
    r1 = client.post(f"{A}/refresh", json={"refresh_token": user["refresh_token"]})
    assert r1.status_code == 200
    new = r1.json()
    assert new["refresh_token"] != user["refresh_token"]
    h_new = {"Authorization": f"Bearer {new['access_token']}"}
    assert client.get(f"{A}/me", headers=h_new).status_code == 200
    # the old refresh token comes back: treated as theft, every session ends
    reuse = client.post(f"{A}/refresh", json={"refresh_token": user["refresh_token"]})
    assert reuse.status_code == 401
    assert client.get(f"{A}/me", headers=h_new).status_code == 401
    assert client.post(f"{A}/refresh", json={"refresh_token": new["refresh_token"]}
                       ).status_code == 401


def test_logout_and_session_revocation_are_immediate(client, user):
    second = client.post(f"{A}/login", json={"email": user["email"], "password": PASSWORD}).json()
    h2 = {"Authorization": f"Bearer {second['access_token']}"}
    listed = client.get(f"{A}/sessions", headers=user["headers"]).json()
    assert len(listed) == 2 and sum(s["current"] for s in listed) == 1
    other = next(s for s in listed if not s["current"])
    assert client.delete(f"{A}/sessions/{other['id']}", headers=user["headers"]).status_code == 204
    assert client.get(f"{A}/me", headers=h2).status_code == 401  # access token dies with session
    assert client.post(f"{A}/logout", headers=user["headers"]).status_code == 204
    assert client.get(f"{A}/me", headers=user["headers"]).status_code == 401


def test_cannot_touch_another_users_session(client, user):
    other = register(client)
    theirs = client.get(f"{A}/sessions", headers=other["headers"]).json()[0]["id"]
    r = client.delete(f"{A}/sessions/{theirs}", headers=user["headers"])
    assert r.status_code == 403
    assert client.get(f"{A}/me", headers=other["headers"]).status_code == 200


def test_auth_required_and_bad_tokens(client):
    assert client.get(f"{A}/me").status_code == 401
    assert client.get(f"{A}/me", headers={"Authorization": "Bearer junk"}).status_code == 401
    assert client.get(f"{A}/sessions/{uuid.uuid4()}").status_code == 405  # only DELETE exists


def test_passwords_are_hashed_and_audit_is_written(client, settings, user):
    engine = create_engine(settings.database_url)
    with engine.connect() as c:
        c.execute(text(f'SET search_path TO "{settings.db_schema}"'))
        stored = c.execute(text("select password_hash from users where email = :e"),
                           {"e": user["email"]}).scalar_one()
        actions = {r[0] for r in c.execute(text("select action from audit_events"))}
    engine.dispose()
    assert stored.startswith("$argon2id$") and PASSWORD not in stored
    assert {"auth.register"} <= actions


def test_login_rate_limit(client, settings):
    client.app.state.limiter.per_minute = 3
    codes = [client.post(f"{A}/login", json={"email": "rl@example.com", "password": "x" * 12}
                         ).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]

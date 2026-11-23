"""MFA (TOTP + recovery codes), password change and password reset."""

from __future__ import annotations

import time

from quantera.core import totp
from tests.platform.conftest import PASSWORD, register

NEW = "another long passphrase 42"


def test_totp_rfc6238_vector():
    # RFC 6238 appendix B, SHA-1 seed "12345678901234567890", T=59 -> 94287082 (8 digits);
    # the 6-digit code is its last six digits.
    import base64

    seed = base64.b32encode(b"12345678901234567890").decode()
    assert totp.code_at(seed, 59) == "287082"
    assert totp.code_at(seed, 1111111109) == "081804"
    s = totp.new_secret()
    now = 1_800_000_000
    step = totp.verify(s, totp.code_at(s, now), last_step=None, now=now)
    assert step is not None
    assert totp.verify(s, totp.code_at(s, now), last_step=step, now=now) is None  # no replay
    assert totp.unseal("k" * 32, totp.seal("k" * 32, s)) == s


def _enable(client, h):
    setup = client.post("/api/v1/auth/mfa/setup", headers=h).json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/Quantera:")
    assert client.post("/api/v1/auth/mfa/enable", headers=h,
                       json={"code": "000000"}).status_code == 400
    r = client.post("/api/v1/auth/mfa/enable", headers=h,
                    json={"code": totp.code_at(setup["secret"], time.time())})
    assert r.status_code == 200, r.text
    return setup["secret"], r.json()["recovery_codes"]


def _login(client, email, password=PASSWORD, code=None):
    return client.post("/api/v1/auth/login",
                       json={"email": email, "password": password, "mfa_code": code})


def test_mfa_login_flow(client, user):
    secret, codes = _enable(client, user["headers"])
    assert len(codes) == 10
    assert client.get("/api/v1/auth/me", headers=user["headers"]).json()["mfa_enabled"]
    r = _login(client, user["email"])
    assert r.status_code == 401 and r.json()["error"]["code"] == "mfa_required"
    assert _login(client, user["email"], code="123456").status_code == 401
    # the code used to enable is already spent (replay refused); a recovery code works once
    assert _login(client, user["email"], code=codes[0]).status_code == 200
    assert _login(client, user["email"], code=codes[0]).status_code == 401
    tok = _login(client, user["email"], code=codes[1]).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/v1/auth/mfa", headers=h).json()["recovery_codes_left"] == 8
    assert client.post("/api/v1/auth/mfa/disable", headers=h,
                       json={"password": "wrong password!!", "code": codes[2]}).status_code == 401
    assert client.post("/api/v1/auth/mfa/disable", headers=h,
                       json={"password": PASSWORD, "code": codes[2]}).status_code == 204
    assert _login(client, user["email"]).status_code == 200
    assert secret  # (kept only to show the secret is returned once at setup)


def test_password_change_ends_sessions(client, user):
    h = user["headers"]
    r = client.post("/api/v1/auth/password", headers=h,
                    json={"current_password": "nope nope nope", "new_password": NEW})
    assert r.status_code == 401
    r = client.post("/api/v1/auth/password", headers=h,
                    json={"current_password": PASSWORD, "new_password": NEW})
    assert r.status_code == 200 and r.json()["sessions_ended"] >= 1
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401
    assert _login(client, user["email"]).status_code == 401
    assert _login(client, user["email"], NEW).status_code == 200


def test_password_reset(client, user):
    unknown = client.post("/api/v1/auth/password-reset/request",
                          json={"email": "nobody-here@example.com"})
    known = client.post("/api/v1/auth/password-reset/request", json={"email": user["email"]})
    assert unknown.status_code == known.status_code == 202
    assert unknown.json()["message"] == known.json()["message"]  # no enumeration
    assert "dev_token" not in unknown.json()
    first = known.json()["dev_token"]
    second = client.post("/api/v1/auth/password-reset/request",
                         json={"email": user["email"]}).json()["dev_token"]
    # only the newest link works
    assert client.post("/api/v1/auth/password-reset/confirm",
                       json={"token": first, "new_password": NEW}).status_code == 401
    assert client.post("/api/v1/auth/password-reset/confirm",
                       json={"token": second, "new_password": "short"}).status_code == 400
    assert client.post("/api/v1/auth/password-reset/confirm",
                       json={"token": second, "new_password": NEW}).status_code == 204
    assert client.post("/api/v1/auth/password-reset/confirm",
                       json={"token": second, "new_password": NEW}).status_code == 401  # once
    assert client.get("/api/v1/auth/me", headers=user["headers"]).status_code == 401
    assert _login(client, user["email"], NEW).status_code == 200


def test_reset_needs_second_factor_when_mfa_on(client):
    u = register(client)
    _, codes = _enable(client, u["headers"])
    tok = client.post("/api/v1/auth/password-reset/request",
                      json={"email": u["email"]}).json()["dev_token"]
    r = client.post("/api/v1/auth/password-reset/confirm", json={"token": tok, "new_password": NEW})
    assert r.status_code == 401 and r.json()["error"]["code"] == "mfa_required"
    assert client.post("/api/v1/auth/password-reset/confirm", json={
        "token": tok, "new_password": NEW, "mfa_code": codes[0]}).status_code == 204

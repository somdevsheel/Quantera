"""/api/v1/auth: register, login (with MFA), refresh, logout, me, sessions, MFA enrolment,
password change and password reset."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, EmailStr, Field

from quantera.api.deps import PrincipalDep, SessionDep, SettingsDep, request_id
from quantera.identity import account
from quantera.identity import service as identity
from quantera.identity.models import User, Workspace

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    full_name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    mfa_code: str | None = Field(default=None, max_length=40)  # TOTP or a recovery code


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=40)


class MfaDisableIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=6, max_length=40)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class ResetRequestIn(BaseModel):
    email: EmailStr


class ResetConfirmIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=1, max_length=256)
    mfa_code: str | None = Field(default=None, max_length=40)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class TokensOut(BaseModel):
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a secret
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    mfa_enabled: bool
    workspace: dict[str, str]
    role: str


class SessionOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    user_agent: str | None
    current: bool


def _tokens(t: identity.Tokens) -> TokensOut:
    return TokensOut(access_token=t.access_token, access_expires_at=t.access_expires_at,
                     refresh_token=t.refresh_token, refresh_expires_at=t.refresh_expires_at)


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, request: Request, session: SessionDep,
             settings: SettingsDep) -> TokensOut:
    """Create an account with a private personal workspace (role OWNER)."""
    request.app.state.limiter.check(f"register:{request.client.host if request.client else ''}")
    _, t = identity.register(session, settings, email=body.email, password=body.password,
                             full_name=body.full_name, user_agent=request.headers.get("user-agent"),
                             request_id=request_id(request))
    return _tokens(t)


@router.post("/login")
def login(body: LoginIn, request: Request, session: SessionDep, settings: SettingsDep) -> TokensOut:
    t = identity.login(session, settings, request.app.state.limiter, email=body.email,
                       password=body.password,
                       client_ip=request.client.host if request.client else None,
                       user_agent=request.headers.get("user-agent"),
                       request_id=request_id(request), mfa_code=body.mfa_code)
    return _tokens(t)


@router.post("/refresh")
def refresh(body: RefreshIn, request: Request, session: SessionDep,
            settings: SettingsDep) -> TokensOut:
    """Rotate the refresh token. Re-using an old one ends every session of the account."""
    return _tokens(identity.refresh(session, settings, body.refresh_token, request_id(request)))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, p: PrincipalDep, session: SessionDep) -> None:
    identity.logout(session, p, request_id(request))


@router.get("/me")
def me(p: PrincipalDep, session: SessionDep) -> MeOut:
    user = session.get(User, p.user_id)
    ws = session.get(Workspace, p.workspace_id)
    assert user is not None and ws is not None
    return MeOut(id=user.id, email=user.email, full_name=user.full_name,
                 mfa_enabled=user.mfa_enabled, workspace={"id": str(ws.id), "name": ws.name},
                 role=p.role.value)


@router.get("/sessions")
def sessions(p: PrincipalDep, session: SessionDep) -> list[SessionOut]:
    return [SessionOut(id=s.id, created_at=s.created_at, last_used_at=s.last_used_at,
                       expires_at=s.expires_at, user_agent=s.user_agent,
                       current=s.id == p.session_id)
            for s in identity.list_sessions(session, p)]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke(session_id: uuid.UUID, request: Request, p: PrincipalDep, session: SessionDep) -> None:
    identity.revoke_session(session, p, session_id, request_id(request))



@router.post("/mfa/setup")
def mfa_setup(p: PrincipalDep, session: SessionDep, settings: SettingsDep) -> dict[str, str]:
    """A new authenticator secret (shown once). MFA turns on only after /mfa/enable."""
    return account.mfa_setup(session, settings, p)


@router.post("/mfa/enable")
def mfa_enable(body: CodeIn, p: PrincipalDep, session: SessionDep,
               settings: SettingsDep) -> dict[str, Any]:
    codes = account.mfa_enable(session, settings, p, body.code)
    return {"mfa_enabled": True, "recovery_codes": codes,
            "note": "Store these recovery codes safely; each works once and they are not shown "
                    "again."}


@router.post("/mfa/disable", status_code=status.HTTP_204_NO_CONTENT)
def mfa_disable(body: MfaDisableIn, p: PrincipalDep, session: SessionDep,
                settings: SettingsDep) -> None:
    account.mfa_disable(session, settings, p, body.password, body.code)


@router.get("/mfa")
def mfa_status(p: PrincipalDep, session: SessionDep) -> dict[str, Any]:
    user = session.get(User, p.user_id)
    assert user is not None
    return {"mfa_enabled": user.mfa_enabled,
            "recovery_codes_left": account.recovery_codes_left(session, p)}


@router.post("/password")
def change_password(body: PasswordChangeIn, p: PrincipalDep, session: SessionDep,
                    settings: SettingsDep) -> dict[str, Any]:
    """Changes the password and signs out every session, this one included."""
    n = account.change_password(session, settings, p, body.current_password, body.new_password)
    return {"changed": True, "sessions_ended": n}


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def reset_request(body: ResetRequestIn, request: Request, session: SessionDep,
                  settings: SettingsDep) -> dict[str, Any]:
    """Same answer whether or not the email is registered."""
    raw = account.request_reset(session, settings, request.app.state.limiter, body.email,
                                request.client.host if request.client else None)
    out: dict[str, Any] = {"message": "If that email has an account, a reset link is on its way."}
    if raw is not None:
        out["dev_token"] = raw  # never in production; see quantera/identity/account.py
    return out


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def reset_confirm(body: ResetConfirmIn, session: SessionDep, settings: SettingsDep) -> None:
    account.confirm_reset(session, settings, body.token, body.new_password, body.mfa_code)

"""Registration, login, token refresh (rotation with reuse detection), logout, revocation."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from quantera.core import audit
from quantera.core import security as sec
from quantera.core.config import Settings
from quantera.core.errors import Conflict, DomainError, Forbidden, RateLimited, Unauthorized
from quantera.identity.models import Membership, Role, User, UserSession, Workspace

# Argon2 hash of a random value: verifying against it keeps failed logins for unknown emails as
# slow as for known ones, so response time does not reveal which emails are registered.
_DUMMY_HASH = sec.hash_password(uuid.uuid4().hex)


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    workspace_id: uuid.UUID
    session_id: uuid.UUID
    role: Role

    def can_write(self) -> bool:
        return self.role in (Role.OWNER, Role.MEMBER)


@dataclass(frozen=True)
class Tokens:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime


class RateLimiter:
    """In-process sliding window (per API process). Use Redis when running several processes."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            raise RateLimited("Too many attempts; try again in a minute")
        q.append(now)


def _now() -> datetime:
    return datetime.now(UTC)


def _normalise_email(email: str) -> str:
    return email.strip().lower()


def _check_password(settings: Settings, password: str, email: str) -> None:
    if len(password) < settings.password_min_length:
        raise DomainError(f"Password must be at least {settings.password_min_length} characters")
    if email.split("@")[0] in password.lower():
        raise DomainError("Password must not contain your email name")


def _open_session(session: Session, settings: Settings, user: User, workspace_id: uuid.UUID,
                  user_agent: str | None) -> Tokens:
    raw, digest = sec.new_refresh_token()
    s = UserSession(user_id=user.id, workspace_id=workspace_id, refresh_hash=digest,
                    expires_at=_now() + timedelta(seconds=settings.refresh_token_ttl_seconds),
                    user_agent=(user_agent or "")[:200] or None)
    session.add(s)
    session.flush()
    access, exp = sec.issue_access_token(settings.jwt_secret, user_id=user.id, session_id=s.id,
                                         workspace_id=workspace_id,
                                         ttl_seconds=settings.access_token_ttl_seconds)
    return Tokens(access, exp, raw, s.expires_at)


def register(session: Session, settings: Settings, *, email: str, password: str, full_name: str,
             user_agent: str | None = None, request_id: str | None = None) -> tuple[User, Tokens]:
    email = _normalise_email(email)
    _check_password(settings, password, email)
    if session.scalar(select(User.id).where(User.email == email)) is not None:
        raise Conflict("An account with this email already exists")
    user = User(email=email, password_hash=sec.hash_password(password), full_name=full_name.strip())
    session.add(user)
    session.flush()
    ws = Workspace(name=f"{user.full_name}'s workspace", owner_id=user.id, personal=True)
    session.add(ws)
    session.flush()
    session.add(Membership(workspace_id=ws.id, user_id=user.id, role=Role.OWNER.value))
    tokens = _open_session(session, settings, user, ws.id, user_agent)
    audit.record(session, "auth.register", actor=user.id, workspace=ws.id, resource_type="user",
                 resource_id=user.id, request_id=request_id)
    return user, tokens


def login(session: Session, settings: Settings, limiter: RateLimiter, *, email: str,
          password: str, client_ip: str | None, user_agent: str | None = None,
          request_id: str | None = None, mfa_code: str | None = None) -> Tokens:
    email = _normalise_email(email)
    limiter.check(f"ip:{client_ip}")
    limiter.check(f"email:{email}")
    user = session.scalar(select(User).where(User.email == email))
    ok = sec.verify_password(user.password_hash if user else _DUMMY_HASH, password)
    if user is None or not ok or user.status != "ACTIVE":
        audit.record(session, "auth.login", actor=user.id if user else None, result="failure",
                     request_id=request_id, details={"reason": "bad_credentials"})
        session.commit()  # keep the failure record even though the request fails
        raise Unauthorized("Email or password is incorrect")
    if user.mfa_enabled:
        from quantera.identity.account import check_second_factor

        try:
            how = check_second_factor(session, settings, user, mfa_code)
        except Unauthorized:
            audit.record(session, "auth.login", actor=user.id, result="failure",
                         request_id=request_id, details={"reason": "bad_mfa_code"})
            session.commit()
            raise
        audit.record(session, "auth.mfa_verified", actor=user.id, details={"method": how})
    if sec.needs_rehash(user.password_hash):
        user.password_hash = sec.hash_password(password)
    ws_id = session.scalar(select(Workspace.id).where(Workspace.owner_id == user.id,
                                                      Workspace.personal.is_(True)))
    assert ws_id is not None
    user.last_login_at = _now()
    tokens = _open_session(session, settings, user, ws_id, user_agent)
    audit.record(session, "auth.login", actor=user.id, workspace=ws_id, request_id=request_id)
    return tokens


def refresh(session: Session, settings: Settings, raw: str,
            request_id: str | None = None) -> Tokens:
    digest = sec.hash_token(raw)
    s = session.scalar(select(UserSession).where(UserSession.refresh_hash == digest))
    if s is None:
        reused = session.scalar(select(UserSession).where(
            UserSession.previous_refresh_hash == digest))
        if reused is not None:  # an old token came back: assume theft, end every session
            revoke_all(session, reused.user_id, reason="refresh_reuse")
            audit.record(session, "auth.refresh_reuse", actor=reused.user_id, result="failure",
                         request_id=request_id)
            session.commit()
        raise Unauthorized("Invalid refresh token")
    if s.revoked_at is not None or s.expires_at <= _now():
        raise Unauthorized("Session has ended; sign in again")
    user = session.get(User, s.user_id)
    if user is None or user.status != "ACTIVE":
        raise Unauthorized("Account is not active")
    new_raw, new_digest = sec.new_refresh_token()
    s.previous_refresh_hash, s.refresh_hash, s.last_used_at = s.refresh_hash, new_digest, _now()
    access, exp = sec.issue_access_token(settings.jwt_secret, user_id=user.id, session_id=s.id,
                                         workspace_id=s.workspace_id,
                                         ttl_seconds=settings.access_token_ttl_seconds)
    return Tokens(access, exp, new_raw, s.expires_at)


def logout(session: Session, p: Principal, request_id: str | None = None) -> None:
    session.execute(update(UserSession).where(UserSession.id == p.session_id).values(
        revoked_at=_now(), revoked_reason="logout"))
    audit.record(session, "auth.logout", actor=p.user_id, workspace=p.workspace_id,
                 request_id=request_id)


def revoke_all(session: Session, user_id: uuid.UUID, reason: str) -> int:
    res = session.execute(update(UserSession).where(
        UserSession.user_id == user_id, UserSession.revoked_at.is_(None)).values(
        revoked_at=_now(), revoked_reason=reason))
    return int(res.rowcount or 0)  # type: ignore[attr-defined]


def list_sessions(session: Session, p: Principal) -> list[UserSession]:
    return list(session.scalars(select(UserSession).where(
        UserSession.user_id == p.user_id, UserSession.revoked_at.is_(None),
        UserSession.expires_at > _now()).order_by(UserSession.created_at.desc())))


def revoke_session(session: Session, p: Principal, session_id: uuid.UUID,
                   request_id: str | None = None) -> None:
    s = session.get(UserSession, session_id)
    if s is None or s.user_id != p.user_id:  # never reveal other users' sessions
        raise Forbidden("Not your session")
    s.revoked_at, s.revoked_reason = _now(), "revoked_by_user"
    audit.record(session, "auth.session_revoked", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="session", resource_id=session_id, request_id=request_id)


def authenticate(session: Session, settings: Settings, token: str) -> Principal:
    """Validate an access token AND its server-side session (so revocation is immediate)."""
    claims = sec.decode_access_token(settings.jwt_secret, token)
    sid, uid, wid = uuid.UUID(claims["sid"]), uuid.UUID(claims["sub"]), uuid.UUID(claims["wid"])
    s = session.get(UserSession, sid)
    if s is None or s.user_id != uid or s.revoked_at is not None or s.expires_at <= _now():
        raise Unauthorized("Session has ended; sign in again")
    role = session.scalar(select(Membership.role).where(Membership.workspace_id == wid,
                                                        Membership.user_id == uid))
    if role is None:
        raise Unauthorized("No access to this workspace")
    return Principal(uid, wid, sid, Role(role))

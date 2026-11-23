"""Second factor (TOTP plus single-use recovery codes), password change and password reset.

Password reset never reveals whether an email is registered. A completed reset or change ends
every session of the account. No email service is configured: outside production the reset
token is returned to the caller so the flow can be exercised; in production it is only logged
as "would send" without the token, and a mail adapter must be configured to deliver it.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantera.core import audit, totp
from quantera.core import security as sec
from quantera.core.config import Settings
from quantera.core.errors import Conflict, DomainError, Unauthorized
from quantera.identity.models import MfaRecoveryCode, PasswordReset, User
from quantera.identity.service import (
    Principal,
    RateLimiter,
    _check_password,
    _normalise_email,
    revoke_all,
)

log = logging.getLogger(__name__)
RECOVERY_CODES = 10


class MfaRequired(DomainError):
    status, code = 401, "mfa_required"


def _key(settings: Settings) -> str:
    return settings.mfa_key or settings.jwt_secret


def _now() -> datetime:
    return datetime.now(UTC)


def _user(session: Session, p: Principal) -> User:
    u = session.get(User, p.user_id)
    assert u is not None
    return u


# --------------------------------------------------------------------------- MFA
def mfa_setup(session: Session, settings: Settings, p: Principal) -> dict[str, str]:
    """Start (or restart) enrolment. MFA is not on until a code is confirmed."""
    u = _user(session, p)
    if u.mfa_enabled:
        raise Conflict("MFA is already enabled; disable it first to enrol a new device")
    s = totp.new_secret()
    u.mfa_secret, u.mfa_last_step = totp.seal(_key(settings), s), None
    return {"secret": s, "otpauth_uri": totp.uri(s, u.email)}


def mfa_enable(session: Session, settings: Settings, p: Principal, code: str) -> list[str]:
    u = _user(session, p)
    if u.mfa_enabled:
        raise Conflict("MFA is already enabled")
    if not u.mfa_secret:
        raise DomainError("Start MFA setup first")
    step = totp.verify(totp.unseal(_key(settings), u.mfa_secret), code, last_step=None)
    if step is None:
        raise DomainError("That code is not valid; check the device clock and try again")
    u.mfa_enabled, u.mfa_last_step = True, step
    for old in session.scalars(select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == u.id)):
        session.delete(old)
    codes = [f"{secrets.token_hex(4)}-{secrets.token_hex(4)}" for _ in range(RECOVERY_CODES)]
    session.add_all(MfaRecoveryCode(user_id=u.id, code_hash=sec.hash_token(c)) for c in codes)
    audit.record(session, "auth.mfa_enable", actor=u.id, workspace=p.workspace_id)
    return codes  # shown once


def check_second_factor(session: Session, settings: Settings, u: User, code: str | None) -> str:
    """Raises unless `code` is a fresh TOTP code or an unused recovery code. Returns which."""
    if not code:
        raise MfaRequired("Enter the code from your authenticator app")
    if u.mfa_secret:
        step = totp.verify(totp.unseal(_key(settings), u.mfa_secret), code,
                           last_step=u.mfa_last_step)
        if step is not None:
            u.mfa_last_step = step
            return "totp"
    rc = session.scalar(select(MfaRecoveryCode).where(
        MfaRecoveryCode.user_id == u.id, MfaRecoveryCode.used_at.is_(None),
        MfaRecoveryCode.code_hash == sec.hash_token(code.strip().lower())))
    if rc is not None:
        rc.used_at = _now()
        return "recovery_code"
    raise Unauthorized("The code is not valid")


def mfa_disable(session: Session, settings: Settings, p: Principal, password: str,
                code: str) -> None:
    u = _user(session, p)
    if not u.mfa_enabled:
        raise Conflict("MFA is not enabled")
    if not sec.verify_password(u.password_hash, password):
        raise Unauthorized("Password is incorrect")
    check_second_factor(session, settings, u, code)
    u.mfa_enabled, u.mfa_secret, u.mfa_last_step = False, None, None
    for rc in session.scalars(select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == u.id)):
        session.delete(rc)
    audit.record(session, "auth.mfa_disable", actor=u.id, workspace=p.workspace_id)


def recovery_codes_left(session: Session, p: Principal) -> int:
    return len(list(session.scalars(select(MfaRecoveryCode.id).where(
        MfaRecoveryCode.user_id == p.user_id, MfaRecoveryCode.used_at.is_(None)))))


# --------------------------------------------------------------------------- passwords
def change_password(session: Session, settings: Settings, p: Principal, current: str,
                    new: str) -> int:
    u = _user(session, p)
    if not sec.verify_password(u.password_hash, current):
        raise Unauthorized("Current password is incorrect")
    _check_password(settings, new, u.email)
    u.password_hash = sec.hash_password(new)
    n = revoke_all(session, u.id, "password_change")
    audit.record(session, "auth.password_change", actor=u.id, workspace=p.workspace_id)
    return n


def request_reset(session: Session, settings: Settings, limiter: RateLimiter, email: str,
                  client_ip: str | None) -> str | None:
    """Always succeeds from the caller's point of view. Returns the raw token only outside
    production (see the module docstring)."""
    email = _normalise_email(email)
    limiter.check(f"reset-ip:{client_ip}")
    limiter.check(f"reset:{email}")
    u = session.scalar(select(User).where(User.email == email))
    if u is None or u.status != "ACTIVE":
        audit.record(session, "auth.password_reset_request", result="failure",
                     details={"reason": "unknown_or_inactive"})
        return None
    for old in session.scalars(select(PasswordReset).where(
            PasswordReset.user_id == u.id, PasswordReset.used_at.is_(None))):
        old.used_at = _now()  # only the newest link works
    raw, digest = sec.new_refresh_token()
    session.add(PasswordReset(user_id=u.id, token_hash=digest, expires_at=_now() + timedelta(
        seconds=settings.password_reset_ttl_seconds)))
    audit.record(session, "auth.password_reset_request", actor=u.id)
    if settings.environment == "production":
        log.warning("password reset requested but no mail adapter is configured")
        return None
    return raw


def confirm_reset(session: Session, settings: Settings, token: str, new: str,
                  mfa_code: str | None) -> None:
    r = session.scalar(select(PasswordReset).where(
        PasswordReset.token_hash == sec.hash_token(token)))
    if r is None or r.used_at is not None or r.expires_at <= _now():
        raise Unauthorized("This reset link is invalid or has expired")
    u = session.get(User, r.user_id)
    assert u is not None
    if u.mfa_enabled:  # a mailbox alone must not be enough to take over an MFA account
        check_second_factor(session, settings, u, mfa_code)
    _check_password(settings, new, u.email)
    u.password_hash = sec.hash_password(new)
    r.used_at = _now()
    revoke_all(session, u.id, "password_reset")
    audit.record(session, "auth.password_reset", actor=u.id)

"""Password hashing (Argon2id), JWT access tokens and opaque refresh tokens."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from quantera.core.errors import Unauthorized

_hasher = PasswordHasher()  # Argon2id with library defaults (memory-hard)
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored: str, password: str) -> bool:
    try:
        return _hasher.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    return _hasher.check_needs_rehash(stored)


def new_refresh_token() -> tuple[str, str]:
    """(token for the client, sha256 to store). The raw token is never stored."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_access_token(secret: str, *, user_id: uuid.UUID, session_id: uuid.UUID,
                       workspace_id: uuid.UUID, ttl_seconds: int) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    exp = now + timedelta(seconds=ttl_seconds)
    claims = {"sub": str(user_id), "sid": str(session_id), "wid": str(workspace_id),
              "iat": int(now.timestamp()), "exp": int(exp.timestamp()), "typ": "access"}
    return jwt.encode(claims, secret, algorithm=ALGORITHM), exp


def decode_access_token(secret: str, token: str) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(token, secret, algorithms=[ALGORITHM],
                                            options={"require": ["sub", "sid", "exp", "typ"]})
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Access token expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid access token") from exc
    if claims.get("typ") != "access":
        raise Unauthorized("Invalid access token")
    return claims

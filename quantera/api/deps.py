"""Request dependencies: database session, settings, authenticated principal."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from quantera.core.config import Settings
from quantera.core.errors import Forbidden, Unauthorized
from quantera.identity import service as identity

_bearer = HTTPBearer(auto_error=False)


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session(request: Request) -> Iterator[Session]:
    yield from request.app.state.db.session()


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session)]


def get_principal(session: SessionDep, settings: SettingsDep,
                  creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
                  ) -> identity.Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise Unauthorized("Sign in required")
    return identity.authenticate(session, settings, creds.credentials)


PrincipalDep = Annotated[identity.Principal, Depends(get_principal)]


def require_write(p: PrincipalDep) -> identity.Principal:
    if not p.can_write():
        raise Forbidden("Read-only access")
    return p


WriterDep = Annotated[identity.Principal, Depends(require_write)]


def request_id(request: Request) -> str | None:
    rid: str | None = getattr(request.state, "request_id", None)
    return rid

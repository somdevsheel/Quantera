"""/api/v1/workspaces, /api/v1/invites, /api/v1/shares, /api/v1/auth/switch-workspace."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from quantera.api.deps import PrincipalDep, SessionDep, SettingsDep
from quantera.api.routes.auth import TokensOut, _tokens
from quantera.collab import service as svc
from quantera.collab.models import ShareGrant
from quantera.core.errors import NotFound
from quantera.identity import service as identity
from quantera.identity.models import Membership, User

router = APIRouter(tags=["collaboration"])


class TokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class SwitchIn(BaseModel):
    workspace_id: uuid.UUID


def _grant(g: ShareGrant) -> dict[str, Any]:
    return {"id": str(g.id), "resource_type": g.resource_type, "resource_id": str(g.resource_id),
            "grantee_user_id": str(g.grantee_user_id) if g.grantee_user_id else None,
            "grantee_workspace_id": str(g.grantee_workspace_id) if g.grantee_workspace_id
            else None, "expires_at": g.expires_at.isoformat() if g.expires_at else None,
            "revoked": g.revoked_at is not None, "url": f"/api/v1/shared/{g.id}"}


@router.post("/workspaces", status_code=status.HTTP_201_CREATED)
def create_workspace(body: svc.WorkspaceIn, p: PrincipalDep, session: SessionDep
                     ) -> dict[str, Any]:
    ws = svc.create_workspace(session, p, body)
    return {"id": str(ws.id), "name": ws.name, "personal": False, "role": "OWNER"}


@router.get("/workspaces")
def workspaces(p: PrincipalDep, session: SessionDep) -> list[dict[str, Any]]:
    return svc.my_workspaces(session, p)


@router.get("/workspaces/{workspace_id}/members")
def members(workspace_id: uuid.UUID, p: PrincipalDep, session: SessionDep
            ) -> list[dict[str, Any]]:
    return svc.members(session, p, workspace_id)


@router.delete("/workspaces/{workspace_id}/members/{user_id}",
               status_code=status.HTTP_204_NO_CONTENT)
def remove(workspace_id: uuid.UUID, user_id: uuid.UUID, p: PrincipalDep,
           session: SessionDep) -> None:
    svc.remove_member(session, p, workspace_id, user_id)


@router.post("/workspaces/{workspace_id}/invites", status_code=status.HTTP_201_CREATED)
def invite(workspace_id: uuid.UUID, body: svc.InviteIn, p: PrincipalDep,
           session: SessionDep) -> dict[str, Any]:
    """Returns the invitation token ONCE; send it to the invitee (no email service is
    configured). It expires in 7 days and works only for the invited email address."""
    inv, raw = svc.invite(session, p, workspace_id, body)
    return {"id": str(inv.id), "email": inv.email, "role": inv.role, "token": raw,
            "expires_at": inv.expires_at.isoformat()}


@router.delete("/workspaces/{workspace_id}/invites/{invite_id}",
               status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(workspace_id: uuid.UUID, invite_id: uuid.UUID, p: PrincipalDep,
                  session: SessionDep) -> None:
    svc.revoke_invite(session, p, workspace_id, invite_id)


@router.post("/invites/accept")
def accept(body: TokenIn, p: PrincipalDep, session: SessionDep) -> dict[str, Any]:
    m = svc.accept(session, p, body.token)
    return {"workspace_id": str(m.workspace_id), "role": m.role}


@router.post("/auth/switch-workspace")
def switch(body: SwitchIn, request: Request, p: PrincipalDep, session: SessionDep,
           settings: SettingsDep) -> TokensOut:
    """New tokens bound to another workspace you belong to (a new session)."""
    if not session.scalar(select(Membership.id).where(Membership.workspace_id == body.workspace_id,
                                                      Membership.user_id == p.user_id)):
        raise NotFound("Workspace not found")
    user = session.get(User, p.user_id)
    assert user is not None
    return _tokens(identity._open_session(session, settings, user, body.workspace_id,
                                          request.headers.get("user-agent")))


@router.post("/shares", status_code=status.HTTP_201_CREATED)
def share(body: svc.ShareIn, p: PrincipalDep, session: SessionDep) -> dict[str, Any]:
    return _grant(svc.share(session, p, body))


@router.get("/shares")
def my_shares(p: PrincipalDep, session: SessionDep) -> list[dict[str, Any]]:
    return [_grant(g) for g in session.scalars(select(ShareGrant).where(
        ShareGrant.owner_workspace_id == p.workspace_id).order_by(ShareGrant.created_at.desc()))]


@router.delete("/shares/{share_id}")
def revoke(share_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> dict[str, Any]:
    return _grant(svc.revoke_share(session, p, share_id))


@router.get("/shared-with-me")
def shared_with_me(p: PrincipalDep, session: SessionDep) -> list[dict[str, Any]]:
    return [_grant(g) for g in svc.shared_with_me(session, p)]


@router.get("/shared/{share_id}")
def open_share(share_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> dict[str, Any]:
    return svc.open_share(session, p, share_id)

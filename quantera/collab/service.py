"""Group workspaces, invitations, membership, workspace switching and share grants.

Rules: everything is private by default. Only an OWNER or OPERATOR of a workspace invites or
removes members. An invitation is accepted only by a signed-in user whose email matches,
before it expires and unless revoked. A share gives READ access to one watchlist, scan result
or backtest; it can expire and be revoked at any time; portfolios cannot be shared.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from quantera.analysis.models import AnalysisJob, DecisionRecord
from quantera.collab.models import Invitation, ShareGrant
from quantera.core import audit
from quantera.core import security as sec
from quantera.core.errors import Conflict, DomainError, Forbidden, NotFound
from quantera.identity.models import Membership, Role, User, Workspace
from quantera.identity.service import Principal
from quantera.universes.models import Watchlist, WatchlistItem

INVITE_DAYS = 7


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class InviteIn(BaseModel):
    email: EmailStr
    role: Literal["MEMBER", "GUEST"] = "MEMBER"


class ShareIn(BaseModel):
    resource_type: Literal["WATCHLIST", "SCAN", "BACKTEST"]
    resource_id: uuid.UUID
    grantee_email: EmailStr | None = None
    grantee_workspace_id: uuid.UUID | None = None
    expires_in_days: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode="after")
    def _one(self) -> ShareIn:
        if (self.grantee_email is None) == (self.grantee_workspace_id is None):
            raise ValueError("give exactly one of grantee_email or grantee_workspace_id")
        return self


def _now() -> datetime:
    return datetime.now(UTC)


def _admin(session: Session, p: Principal, wid: uuid.UUID) -> Workspace:
    ws = session.get(Workspace, wid)
    role = session.scalar(select(Membership.role).where(Membership.workspace_id == wid,
                                                        Membership.user_id == p.user_id))
    if ws is None or role is None:
        raise NotFound("Workspace not found")
    if role not in (Role.OWNER.value, Role.OPERATOR.value):
        raise Forbidden("Only the workspace owner or an operator can do this")
    return ws


def create_workspace(session: Session, p: Principal, body: WorkspaceIn) -> Workspace:
    ws = Workspace(name=body.name, owner_id=p.user_id, personal=False)
    session.add(ws)
    session.flush()
    session.add(Membership(workspace_id=ws.id, user_id=p.user_id, role=Role.OWNER.value))
    audit.record(session, "workspace.create", actor=p.user_id, workspace=ws.id)
    return ws


def my_workspaces(session: Session, p: Principal) -> list[dict[str, Any]]:
    rows = session.execute(select(Workspace, Membership.role).join(
        Membership, Membership.workspace_id == Workspace.id).where(
        Membership.user_id == p.user_id).order_by(Workspace.personal.desc(), Workspace.name))
    return [{"id": str(w.id), "name": w.name, "personal": w.personal, "role": r,
             "current": w.id == p.workspace_id} for w, r in rows]


def invite(session: Session, p: Principal, wid: uuid.UUID, body: InviteIn
           ) -> tuple[Invitation, str]:
    ws = _admin(session, p, wid)
    if ws.personal:
        raise DomainError("Personal workspaces are private; create a group workspace to invite")
    raw, digest = sec.new_refresh_token()
    inv = Invitation(workspace_id=wid, email=body.email.lower(), role=body.role,
                     token_hash=digest, invited_by=p.user_id,
                     expires_at=_now() + timedelta(days=INVITE_DAYS))
    session.add(inv)
    session.flush()
    audit.record(session, "workspace.invite", actor=p.user_id, workspace=wid,
                 resource_type="invitation", resource_id=inv.id, details={"role": body.role})
    return inv, raw


def accept(session: Session, p: Principal, token: str) -> Membership:
    inv = session.scalar(select(Invitation).where(Invitation.token_hash == sec.hash_token(token)))
    user = session.get(User, p.user_id)
    if inv is None or user is None:
        raise NotFound("Invitation not found")
    if inv.revoked_at is not None:
        raise Conflict("This invitation was revoked")
    if inv.accepted_at is not None:
        raise Conflict("This invitation was already used")
    if inv.expires_at <= _now():
        raise Conflict("This invitation has expired")
    if user.email != inv.email:
        raise Forbidden("This invitation is for a different email address")
    if session.scalar(select(Membership.id).where(Membership.workspace_id == inv.workspace_id,
                                                  Membership.user_id == user.id)):
        raise Conflict("Already a member")
    inv.accepted_at = _now()
    m = Membership(workspace_id=inv.workspace_id, user_id=user.id, role=inv.role)
    session.add(m)
    audit.record(session, "workspace.join", actor=user.id, workspace=inv.workspace_id,
                 details={"role": inv.role})
    return m


def revoke_invite(session: Session, p: Principal, wid: uuid.UUID, iid: uuid.UUID) -> None:
    _admin(session, p, wid)
    inv = session.get(Invitation, iid)
    if inv is None or inv.workspace_id != wid:
        raise NotFound("Invitation not found")
    inv.revoked_at = inv.revoked_at or _now()


def members(session: Session, p: Principal, wid: uuid.UUID) -> list[dict[str, Any]]:
    if not session.scalar(select(Membership.id).where(Membership.workspace_id == wid,
                                                      Membership.user_id == p.user_id)):
        raise NotFound("Workspace not found")
    rows = session.execute(select(User, Membership.role).join(
        Membership, Membership.user_id == User.id).where(Membership.workspace_id == wid))
    return [{"user_id": str(u.id), "email": u.email, "name": u.full_name, "role": r}
            for u, r in rows]


def remove_member(session: Session, p: Principal, wid: uuid.UUID, uid: uuid.UUID) -> None:
    ws = _admin(session, p, wid)
    if uid == ws.owner_id:
        raise Conflict("The owner cannot be removed")
    m = session.scalar(select(Membership).where(Membership.workspace_id == wid,
                                                Membership.user_id == uid))
    if m is None:
        raise NotFound("Not a member")
    session.delete(m)
    audit.record(session, "workspace.remove_member", actor=p.user_id, workspace=wid,
                 resource_type="user", resource_id=uid)


# --------------------------------------------------------------------------- shares
def _resource(session: Session, rtype: str, rid: uuid.UUID) -> Any:
    if rtype == "WATCHLIST":
        return session.get(Watchlist, rid)
    job = session.get(AnalysisJob, rid)
    want = {"SCAN": "SCAN", "BACKTEST": "BACKTEST"}[rtype]
    return job if job is not None and job.kind == want else None


def share(session: Session, p: Principal, body: ShareIn) -> ShareGrant:
    if not p.can_write():
        raise Forbidden("Read-only access")
    res = _resource(session, body.resource_type, body.resource_id)
    if res is None or res.workspace_id != p.workspace_id:
        raise NotFound("Resource not found in your current workspace")
    grantee_user = grantee_ws = None
    if body.grantee_email:
        u = session.scalar(select(User).where(User.email == body.grantee_email.lower()))
        if u is None:
            raise NotFound("No account with that email")  # invite them first
        grantee_user = u.id
    else:
        if not session.scalar(select(Membership.id).where(
                Membership.workspace_id == body.grantee_workspace_id,
                Membership.user_id == p.user_id)):
            raise NotFound("Workspace not found")
        grantee_ws = body.grantee_workspace_id
    g = ShareGrant(owner_workspace_id=p.workspace_id, created_by=p.user_id,
                   resource_type=body.resource_type, resource_id=body.resource_id,
                   grantee_user_id=grantee_user, grantee_workspace_id=grantee_ws,
                   expires_at=_now() + timedelta(days=body.expires_in_days)
                   if body.expires_in_days else None)
    session.add(g)
    session.flush()
    audit.record(session, "share.create", actor=p.user_id, workspace=p.workspace_id,
                 resource_type=body.resource_type.lower(), resource_id=body.resource_id,
                 details={"grant": str(g.id)})
    return g


def revoke_share(session: Session, p: Principal, gid: uuid.UUID) -> ShareGrant:
    g = session.get(ShareGrant, gid)
    if g is None or g.owner_workspace_id != p.workspace_id:
        raise NotFound("Share not found")
    g.revoked_at = g.revoked_at or _now()
    audit.record(session, "share.revoke", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="share", resource_id=gid)
    return g


def _active(q: Any) -> Any:
    return q.where(ShareGrant.revoked_at.is_(None),
                   or_(ShareGrant.expires_at.is_(None), ShareGrant.expires_at > _now()))


def shared_with_me(session: Session, p: Principal) -> list[ShareGrant]:
    return list(session.scalars(_active(select(ShareGrant).where(or_(
        ShareGrant.grantee_user_id == p.user_id,
        ShareGrant.grantee_workspace_id == p.workspace_id)))))


def open_share(session: Session, p: Principal, gid: uuid.UUID) -> dict[str, Any]:
    g = session.scalar(_active(select(ShareGrant).where(ShareGrant.id == gid)))
    mine = g is not None and (g.grantee_user_id == p.user_id
                              or g.grantee_workspace_id == p.workspace_id)
    if g is None or not mine:
        raise NotFound("Share not found")  # revoked, expired or not yours: indistinguishable
    res = _resource(session, g.resource_type, g.resource_id)
    if res is None:
        raise NotFound("The shared item no longer exists")
    body: dict[str, Any] = {"resource_type": g.resource_type, "read_only": True,
                            "expires_at": g.expires_at.isoformat() if g.expires_at else None}
    if g.resource_type == "WATCHLIST":
        items = session.scalars(select(WatchlistItem).where(WatchlistItem.watchlist_id == res.id))
        return body | {"name": res.name, "items": [{"symbol": i.symbol, "exchange": i.exchange,
                                                    "name": i.name} for i in items]}
    if g.resource_type == "SCAN":
        recs = session.scalars(select(DecisionRecord).where(DecisionRecord.job_id == res.id)
                               .order_by(DecisionRecord.rank).limit(200))
        return body | {"as_of": res.as_of.isoformat() if res.as_of else None,
                       "summary": res.summary, "results": [
                           {"rank": r.rank, "symbol": r.symbol, "state": r.state,
                            "opportunity": r.opportunity, "explanation": r.explanation}
                           for r in recs]}
    res_sum = dict(res.summary)
    res_sum.get("result", {}).pop("trades", None)
    return body | {"summary": res_sum}

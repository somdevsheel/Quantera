"""/api/v1/models: training jobs, registry, approval, scoring, monitoring, rollback."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from quantera.analysis import jobs
from quantera.api.deps import PrincipalDep, SessionDep, WriterDep, request_id
from quantera.api.routes.analysis import _job
from quantera.api.routes.universes import LakeDep
from quantera.models_ml import service as svc
from quantera.models_ml.models import MlModel

router = APIRouter(prefix="/models", tags=["models"])


class NoteIn(BaseModel):
    note: str = Field(min_length=3, max_length=500)


@router.post("/train", status_code=status.HTTP_202_ACCEPTED)
def train(body: svc.TrainIn, request: Request, p: WriterDep, session: SessionDep,
          idempotency_key: Annotated[str | None, Header(max_length=80)] = None) -> JSONResponse:
    """Queue a training job: walk-forward against baselines, then CANDIDATE or REJECTED.
    Never promoted automatically."""
    job, created = jobs.enqueue(session, p, "TRAIN", {"request": body.model_dump(mode="json")},
                                idempotency_key=idempotency_key, request_id=request_id(request))
    out = _job(job).model_dump(mode="json")
    return JSONResponse(out, status_code=202 if created else 200)


@router.get("")
def registry(p: PrincipalDep, session: SessionDep, name: str | None = None
             ) -> list[dict[str, Any]]:
    q = select(MlModel).where(MlModel.workspace_id == p.workspace_id)
    if name:
        q = q.where(MlModel.name == name)
    return [svc.summary(m) for m in session.scalars(q.order_by(MlModel.created_at.desc()))]


@router.get("/active")
def active(p: PrincipalDep, session: SessionDep,
           name: Annotated[str, Query(min_length=1)]) -> dict[str, Any]:
    m = svc.champion(session, p, name)
    return ({"state": "APPROVED"} | svc.summary(m)) if m else {"state": "MODEL UNAVAILABLE",
                                                               "name": name}


@router.post("/score")
def score(body: svc.ScoreIn, p: WriterDep, session: SessionDep, lake: LakeDep) -> dict[str, Any]:
    return svc.score(session, p, lake, body)


@router.post("/rollback")
def rollback(body: NoteIn, p: WriterDep, session: SessionDep,
             name: Annotated[str, Query(min_length=1)]) -> dict[str, Any]:
    return svc.rollback(session, p, name, body.note)


@router.get("/{model_id}")
def detail(model_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> dict[str, Any]:
    m = svc.get(session, p, model_id)
    return svc.summary(m) | {"validation": m.validation, "gates_detail": m.gates,
                             "artifact_sha256": m.artifact_sha256, "params": m.params}


@router.post("/{model_id}/approve")
def approve(model_id: uuid.UUID, body: NoteIn, request: Request, p: WriterDep,
            session: SessionDep) -> dict[str, Any]:
    four_eyes = request.app.state.settings.ml_four_eyes
    return svc.summary(svc.approve(session, p, svc.get(session, p, model_id), body.note,
                                   four_eyes))


@router.post("/{model_id}/retire")
def retire(model_id: uuid.UUID, body: NoteIn, p: WriterDep, session: SessionDep
           ) -> dict[str, Any]:
    return svc.summary(svc.retire(session, p, svc.get(session, p, model_id), body.note))


@router.get("/{model_id}/monitor")
def monitor(model_id: uuid.UUID, p: PrincipalDep, session: SessionDep, lake: LakeDep
            ) -> dict[str, Any]:
    return svc.monitor(session, p, lake, svc.get(session, p, model_id))

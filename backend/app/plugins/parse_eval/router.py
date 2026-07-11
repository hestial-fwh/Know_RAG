from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.plugins.parse_eval import service


router = APIRouter(prefix="/parse-eval", tags=["parse-eval"])


class RunEvaluationRequest(BaseModel):
    mode: str = Field(default="no_gt")
    template_id: str = Field(default="generic_no_gt")
    options: dict = Field(default_factory=dict)


@router.get("/templates")
async def templates(_: User = Depends(get_current_user)):
    return service.list_templates()


@router.post("/documents/{doc_id}/run")
async def run_document_evaluation(
    doc_id: str,
    payload: RunEvaluationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.run_evaluation(
        db,
        current_user,
        doc_id,
        mode=payload.mode,
        template_id=payload.template_id,
        options=payload.options,
    )


@router.get("/documents/{doc_id}/latest")
async def latest_document_evaluation(
    doc_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.latest_for_document(db, doc_id)


@router.get("/runs/{run_id}")
async def get_evaluation_run(
    run_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.get_run(db, run_id)


@router.get("/runs/{run_id}/items")
async def list_evaluation_items(
    run_id: str,
    page: int = 1,
    page_size: int = 50,
    level: str = Query("all"),
    issue_type: str = Query("all"),
    target_type: str = Query("all"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.list_items(
        db,
        run_id,
        page=page,
        page_size=page_size,
        level=level,
        issue_type=issue_type,
        target_type=target_type,
    )


@router.get("/runs/{run_id}/report")
async def get_evaluation_report(
    run_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.report(db, run_id)

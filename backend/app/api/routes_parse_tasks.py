from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.services import parse_task_service as service


router = APIRouter(prefix="/parse-tasks", tags=["parse-tasks"])


@router.get("/{task_id}")
async def get_parse_task(
    task_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.get_task(db, task_id)


@router.get("/{task_id}/events")
async def get_parse_task_events(
    task_id: str,
    after_seq: int = Query(0),
    limit: int = Query(200),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.list_events(db, task_id, after_seq, limit)


@router.get("/documents/{doc_id}/latest")
async def latest_document_parse_task(
    doc_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.latest_task_for_document(db, doc_id)

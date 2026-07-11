from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.services import chunk_service as service


router = APIRouter(tags=["chunks"])


@router.get("/documents/{doc_id}/chunks")
async def list_chunks(
    doc_id: str,
    page: int = 1,
    page_size: int = 20,
    chunk_type: str = Query("all", alias="chunk_type"),
    type_filter: Optional[str] = Query(None, alias="type"),
    index_status: str = Query("all", alias="index_status"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    effective_chunk_type = type_filter or chunk_type or "all"
    return service.list_chunks(db, doc_id, page, page_size, chunk_type=effective_chunk_type, index_status=index_status)


@router.get("/documents/{doc_id}/chunk-tree")
async def get_chunk_tree(
    doc_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.get_chunk_tree(db, doc_id)


@router.get("/chunks/by-id/{id}")
async def get_chunk(
    id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.get_chunk(db, id)


@router.put("/chunks/{id}/enable")
async def enable_chunk(
    id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.set_chunk_enabled(db, current_user, id, True)


@router.put("/chunks/{id}/disable")
async def disable_chunk(
    id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.set_chunk_enabled(db, current_user, id, False)


@router.delete("/chunks/{id}")
async def delete_chunk(
    id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.delete_chunk(db, current_user, id)

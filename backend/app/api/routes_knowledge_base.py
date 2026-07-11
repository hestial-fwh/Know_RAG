from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseUpdate
from app.services import embedding_service
from app.services import knowledge_base_service as service


router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


@router.get("")
async def list_knowledge_bases(
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.list_knowledge_bases(db, page, page_size)


@router.post("")
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.create_knowledge_base(db, current_user, payload.model_dump())


@router.get("/{id}")
async def get_knowledge_base(
    id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.get_knowledge_base(db, id)


@router.put("/{id}")
async def update_knowledge_base(
    id: str,
    payload: KnowledgeBaseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.update_knowledge_base(db, current_user, id, payload.model_dump(exclude_none=True))


@router.delete("/{id}")
async def delete_knowledge_base(
    id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.delete_knowledge_base(db, current_user, id)


@router.post("/{kb_id}/embedding")
async def embed_knowledge_base(
    kb_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await embedding_service.embed_knowledge_base(db, current_user, kb_id)


@router.get("/{kb_id}/embedding/status")
async def knowledge_base_embedding_status(
    kb_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return embedding_service.knowledge_base_embedding_status(db, kb_id)

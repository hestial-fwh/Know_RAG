from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.services import document_service as service
from app.services import embedding_service
from app.services.batch_vectorize_service import get_batch_status, start_batch_vectorize


router = APIRouter(tags=["documents"])


class VectorizeBatchRequest(BaseModel):
    document_ids: Optional[list[str]] = None
    only_unvectorized: bool = True
    force: bool = False


@router.post("/documents/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    knowledge_base_id: str = Form(...),
    title: str | None = Form(default=None),
    parse_immediately: bool = Form(default=True),
    parser_engine: str | None = Form(default=None),
    mineru_profile: str | None = Form(default=None),
    enable_ocr: str | None = Form(default=None),
    enable_vlm: bool | None = Form(default=None),
    enable_media_crop: bool | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await service.upload_document(
        db,
        current_user,
        file,
        knowledge_base_id,
        title,
        parse_immediately,
        background_tasks=background_tasks,
        parser_engine=parser_engine,
        mineru_profile=mineru_profile,
        enable_ocr=enable_ocr,
        enable_vlm=enable_vlm,
        enable_media_crop=enable_media_crop,
    )


@router.get("/knowledge-bases/{kb_id}/documents")
async def list_documents(
    kb_id: str,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.list_documents(db, kb_id, page, page_size)


@router.get("/documents/{id}")
async def get_document(
    id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.get_document(db, id)


@router.delete("/documents/{id}")
async def delete_document(
    id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.delete_document(db, current_user, id)


@router.post("/documents/{id}/parse")
async def parse_document(
    background_tasks: BackgroundTasks,
    id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.start_parse_document_task(db, current_user, id, background_tasks, reparse=False)


@router.post("/documents/{id}/reparse")
async def reparse_document(
    background_tasks: BackgroundTasks,
    id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.start_parse_document_task(db, current_user, id, background_tasks, reparse=True)


@router.get("/documents/{id}/status")
async def document_status(
    id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.document_status(db, id)


@router.post("/documents/{id}/embedding")
async def embed_document(
    id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await embedding_service.embed_document(db, current_user, id)


@router.get("/documents/{id}/embedding/status")
async def document_embedding_status(
    id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return embedding_service.document_embedding_status(db, id)


@router.post("/knowledge-bases/{kb_id}/vectorize-batch")
async def vectorize_batch_start(
    kb_id: str,
    payload: VectorizeBatchRequest = VectorizeBatchRequest(),
    _: User = Depends(get_current_user),
):
    return start_batch_vectorize(
        kb_id,
        document_ids=payload.document_ids,
        only_unvectorized=payload.only_unvectorized,
        force=payload.force,
    )


@router.get("/vectorize-batch/{batch_id}")
async def vectorize_batch_status(
    batch_id: str,
    _: User = Depends(get_current_user),
):
    return get_batch_status(batch_id)

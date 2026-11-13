"""派生索引维护 API；构建请求只入队，不能在 HTTP 生命周期内调用模型。"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.response import error_response, success_response
from app.core.security import get_current_user
from app.db.models import Document, KnowledgeBase, KnowledgeIndexJob, User
from app.db.session import get_db
from app.services.knowledge_index.service import (
    enabled, enqueue_document, index_status, reconcile, retry_incomplete, valid_evidence,
)
from app.services.knowledge_index.communities import current_reports, enqueue_graph

router = APIRouter(prefix="/knowledge-bases/{kb_id}/knowledge-index", tags=["knowledge-index"])


class BuildRequest(BaseModel):
    document_id: str | None = None
    retry: bool = False
    limit: int = Field(default=100, ge=1, le=1000)


@router.post("/build")
def build(kb_id: str, payload: BuildRequest, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or not enabled(kb):
        return error_response("知识库不存在或尚未开启实体、关系或事件索引")
    if payload.document_id:
        document = db.get(Document, payload.document_id)
        if document is None or document.knowledge_base_id != kb_id:
            return error_response("当前知识库内不存在此文档")
        job, created = enqueue_document(db, document, retry=payload.retry)
        db.commit()
        return success_response({"job_id": job.id if job else None, "created": created})
    count = reconcile(db, kb_id, limit=payload.limit)
    retried = retry_incomplete(db, kb_id, limit=payload.limit - count) if payload.retry else 0
    db.commit()
    return success_response({"created_jobs": count, "retried_jobs": retried})


@router.get("/status")
def status(kb_id: str, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    if db.get(KnowledgeBase, kb_id) is None:
        return error_response("知识库不存在")
    return success_response(index_status(db, kb_id))


@router.get("/jobs")
def jobs(kb_id: str, limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    rows = db.scalars(select(KnowledgeIndexJob).where(
        KnowledgeIndexJob.knowledge_base_id == kb_id,
    ).order_by(KnowledgeIndexJob.created_at.desc()).limit(limit)).all()
    # 不返回 input_json，避免通过运维接口暴露整个来源快照和模型地址。
    return success_response({"items": [{
        "id": job.id, "document_id": job.document_id, "state": job.state,
        "attempts": job.attempts, "error": job.error, "lease_until": job.lease_until,
    } for job in rows]})


@router.get("/entities")
def entities(kb_id: str, name: str | None = None, limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    return success_response({"items": valid_evidence(db, kb_id, name=name, limit=limit)})


@router.get("/graph")
def graph(kb_id: str, q: str = Query("", max_length=100), focus: str | None = Query(None, max_length=128),
          limit: int = Query(80, ge=1, le=150), db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    from app.services.knowledge_index.graph_view import knowledge_graph

    if db.get(KnowledgeBase, kb_id) is None:
        return error_response("知识库不存在")
    return success_response(knowledge_graph(db, kb_id, query=q.strip(), focus=focus, limit=limit))


@router.post("/communities/build")
def build_communities(kb_id: str, payload: BuildRequest, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    if payload.document_id:
        return error_response("社区按知识库构建，不能指定单篇文档")
    job, created = enqueue_graph(db, kb_id, retry=payload.retry)
    db.commit()
    if job is None:
        return error_response("社区能力未开启或没有有效关系索引")
    return success_response({"graph_job_id": job.id, "created": created, "state": job.state})


@router.get("/communities")
def community_reports(kb_id: str, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    reports, status = current_reports(db, kb_id)
    return success_response({"items": [{key: value for key, value in report.items() if key != "sources"}
        for report in reports], "status": {key: value for key, value in status.items() if key != "model"}})

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.response import error_response, success_response
from app.db.models import Document, KnowledgeBase, User
from app.services.audit_service import record_audit
from app.services.storage import delete_knowledge_base_objects
from app.utils.ids import new_id
from app.services.storage import delete_knowledge_base_cache

def knowledge_base_to_dict(kb: KnowledgeBase) -> dict:
    return {
        "id": kb.id,
        "name": kb.name,
        "description": kb.description,
        "embedding_model_id": kb.embedding_model_id,
        "summary_model_id": kb.summary_model_id,
        "document_count": kb.document_count,
        "chunk_count": kb.chunk_count,
        "created_at": kb.created_at,
        "updated_at": kb.updated_at,
    }


def list_knowledge_bases(db: Session, page: int = 1, page_size: int = 20):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    total = db.scalar(select(func.count()).select_from(KnowledgeBase)) or 0
    items = db.scalars(
        select(KnowledgeBase)
        .order_by(KnowledgeBase.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return success_response(
        {
            "items": [knowledge_base_to_dict(item) for item in items],
            "page": page,
            "page_size": page_size,
            "total": total,
        }
    )


def create_knowledge_base(db: Session, actor: User, payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        return error_response("知识库名称不能为空")
    kb = KnowledgeBase(
        id=new_id(),
        name=name,
        description=payload.get("description") or "",
        embedding_model_id=payload.get("embedding_model_id") or "",
        summary_model_id=payload.get("summary_model_id") or "",
    )
    db.add(kb)
    db.flush()
    record_audit(
        db,
        actor,
        "create_knowledge_base",
        "knowledge_base",
        kb.id,
        request_summary=payload,
        result_summary={"id": kb.id},
    )
    db.commit()
    db.refresh(kb)
    return success_response(knowledge_base_to_dict(kb), "创建成功")


def get_knowledge_base(db: Session, kb_id: str):
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        return error_response(f"知识库不存在：{kb_id}")
    return success_response(knowledge_base_to_dict(kb))


def update_knowledge_base(db: Session, actor: User, kb_id: str, payload: dict):
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        return error_response(f"知识库不存在：{kb_id}")
    if "name" in payload:
        name = (payload.get("name") or "").strip()
        if not name:
            return error_response("知识库名称不能为空")
        kb.name = name
    if "description" in payload:
        kb.description = payload.get("description") or ""
    if "embedding_model_id" in payload:
        kb.embedding_model_id = payload.get("embedding_model_id") or ""
    if "summary_model_id" in payload:
        kb.summary_model_id = payload.get("summary_model_id") or ""
    record_audit(
        db,
        actor,
        "update_knowledge_base",
        "knowledge_base",
        kb.id,
        request_summary=payload,
        result_summary={"id": kb.id},
    )
    db.commit()
    db.refresh(kb)
    return success_response(knowledge_base_to_dict(kb), "保存成功")


def delete_knowledge_base(db: Session, actor: User, kb_id: str):
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        return error_response(f"知识库不存在：{kb_id}")
    document_count = db.scalar(select(func.count()).select_from(Document).where(Document.knowledge_base_id == kb_id)) or 0
    try:
        deleted_objects = delete_knowledge_base_objects(kb_id)
    except Exception as exc:
        return error_response(f"MinIO 知识库对象删除失败：{str(exc)[:200]}")

    try:
        from app.services.keyword_index_service import delete_knowledge_base_from_index

        delete_knowledge_base_from_index(kb_id)
    except Exception:
        pass

    db.delete(kb)
    record_audit(
        db,
        actor,
        "delete_knowledge_base",
        "knowledge_base",
        kb_id,
        result_summary={"deleted": True, "document_count": int(document_count), "minio_deleted_objects": deleted_objects},
    )
    db.commit()
    try:
        delete_knowledge_base_cache(kb_id)
    except Exception:
        pass
    return success_response({"id": kb_id, "deleted": True, "storage_deleted": True, "minio_deleted_objects": deleted_objects}, "删除成功")

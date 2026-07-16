from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.response import error_response, success_response
from app.db.models import Document, ParseTask, ParseTaskEvent, User
from app.utils.ids import new_id
from app.utils.text import sanitize_json_for_postgres, sanitize_text_for_postgres


STAGE_LABELS = {
    "queued": "排队中",
    "prepare": "文件接收",
    "parse": "文档解析",
    "ocr": "OCR 增强",
    "vlm": "图片理解",
    "chunk": "切片入库",
    "embedding": "向量化",
    "done": "完成",
    "failed": "失败",
}

# Block/chunk previews duplicate final parsed data and can dominate database
# storage on large documents. They are intentionally never persisted.
NON_PERSISTED_EVENT_TYPES = {"parsed_block", "chunk_created"}
TERMINAL_TASK_STATUSES = {"completed", "failed"}


def _now():
    return datetime.now(timezone.utc)


def task_to_dict(task: ParseTask, *, event_count: int | None = None) -> dict:
    return {
        "id": task.id,
        "task_id": task.id,
        "document_id": task.document_id,
        "knowledge_base_id": task.knowledge_base_id,
        "task_type": task.task_type,
        "status": task.status,
        "current_stage": task.current_stage,
        "current_stage_label": STAGE_LABELS.get(task.current_stage, task.current_stage),
        "progress": float(task.progress or 0),
        "total_steps": task.total_steps or 0,
        "completed_steps": task.completed_steps or 0,
        "stats": sanitize_json_for_postgres(task.stats or {}),
        "error_message": sanitize_text_for_postgres(task.error_message or ""),
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "created_by": task.created_by or "",
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "event_count": event_count,
    }


def event_to_dict(event: ParseTaskEvent) -> dict:
    return {
        "id": event.id,
        "task_id": event.task_id,
        "document_id": event.document_id,
        "seq_no": event.seq_no,
        "event_type": event.event_type,
        "stage": event.stage,
        "stage_label": STAGE_LABELS.get(event.stage, event.stage),
        "message": sanitize_text_for_postgres(event.message),
        "payload": sanitize_json_for_postgres(event.payload_json or {}),
        "created_at": event.created_at,
    }


def create_parse_task(db: Session, document: Document, actor: User | None, *, task_type: str = "parse", commit: bool = True) -> ParseTask:
    task = ParseTask(
        id=new_id(),
        document_id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        task_type=task_type,
        status="queued",
        current_stage="queued",
        progress=0,
        total_steps=7,
        completed_steps=0,
        stats=sanitize_json_for_postgres({
            "file_name": document.file_name,
            "file_type": document.file_type,
            "file_size": document.file_size,
            "auto_vectorize": False,
            "note": "解析工作台默认只解析和入库，不自动向量化。"
        }),
        created_by=getattr(actor, "id", "") or "",
    )
    db.add(task)
    db.flush()
    add_event(
        db,
        task,
        "task_queued",
        "queued",
        f"已创建解析任务：{document.file_name}",
        {"document_id": document.id, "file_name": document.file_name, "file_type": document.file_type},
        commit=False,
    )
    if commit:
        db.commit()
        db.refresh(task)
    return task


def _next_seq(db: Session, task_id: str) -> int:
    max_seq = db.scalar(select(func.max(ParseTaskEvent.seq_no)).where(ParseTaskEvent.task_id == task_id))
    return int(max_seq or 0) + 1


def add_event(
    db: Session,
    task: ParseTask | str,
    event_type: str,
    stage: str,
    message: str,
    payload: dict[str, Any] | None = None,
    *,
    commit: bool = True,
) -> ParseTaskEvent | None:
    if event_type in NON_PERSISTED_EVENT_TYPES:
        return None
    task_obj = db.get(ParseTask, task) if isinstance(task, str) else task
    if task_obj is None or task_obj.status in TERMINAL_TASK_STATUSES:
        return None
    event = ParseTaskEvent(
        id=new_id(),
        task_id=task_obj.id,
        document_id=task_obj.document_id,
        seq_no=_next_seq(db, task_obj.id),
        event_type=event_type,
        stage=stage,
        message=sanitize_text_for_postgres(message)[:1000],
        payload_json=sanitize_json_for_postgres(payload or {}),
    )
    db.add(event)
    if commit:
        db.commit()
        db.refresh(event)
    return event


def update_task(
    db: Session,
    task: ParseTask | str | None,
    *,
    status: str | None = None,
    stage: str | None = None,
    progress: float | None = None,
    completed_steps: int | None = None,
    total_steps: int | None = None,
    stats: dict[str, Any] | None = None,
    error_message: str | None = None,
    commit: bool = True,
) -> ParseTask | None:
    if task is None:
        return None
    task_obj = db.get(ParseTask, task) if isinstance(task, str) else task
    if task_obj is None:
        return None
    if status is not None:
        task_obj.status = status
        if status == "running" and task_obj.started_at is None:
            task_obj.started_at = _now()
        if status in {"completed", "failed"}:
            task_obj.finished_at = _now()
            db.execute(delete(ParseTaskEvent).where(ParseTaskEvent.task_id == task_obj.id))
    if stage is not None:
        task_obj.current_stage = stage
    if progress is not None:
        task_obj.progress = max(0.0, min(100.0, float(progress)))
    if completed_steps is not None:
        task_obj.completed_steps = int(completed_steps)
    if total_steps is not None:
        task_obj.total_steps = int(total_steps)
    if stats is not None:
        merged = dict(task_obj.stats or {})
        merged.update(sanitize_json_for_postgres(stats))
        task_obj.stats = sanitize_json_for_postgres(merged)
    if error_message is not None:
        task_obj.error_message = sanitize_text_for_postgres(error_message)[:1000]
    if commit:
        db.commit()
        db.refresh(task_obj)
    return task_obj


def get_task(db: Session, task_id: str):
    task = db.get(ParseTask, task_id)
    if task is None:
        return error_response(f"解析任务不存在：{task_id}")
    count = db.scalar(select(func.count()).select_from(ParseTaskEvent).where(ParseTaskEvent.task_id == task_id)) or 0
    return success_response(task_to_dict(task, event_count=int(count)))


def latest_task_for_document(db: Session, doc_id: str):
    task = db.scalars(
        select(ParseTask).where(ParseTask.document_id == doc_id).order_by(ParseTask.created_at.desc()).limit(1)
    ).first()
    if task is None:
        return success_response(None)
    return success_response(task_to_dict(task))


def list_events(db: Session, task_id: str, after_seq: int = 0, limit: int = 200):
    task = db.get(ParseTask, task_id)
    if task is None:
        return error_response(f"解析任务不存在：{task_id}")
    rows = db.scalars(
        select(ParseTaskEvent)
        .where(ParseTaskEvent.task_id == task_id, ParseTaskEvent.seq_no > max(after_seq, 0))
        .order_by(ParseTaskEvent.seq_no.asc())
        .limit(min(max(limit, 1), 500))
    ).all()
    return success_response({"task": task_to_dict(task), "items": [event_to_dict(item) for item in rows]})

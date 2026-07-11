"""Batch vectorization service.

Merges single-document embedding calls into a trackable batch pipeline.

Supports:
- Filtering by ``document_ids``
- ``only_unvectorized`` mode (skip docs that already have all chunks embedded)
- ``force`` re-vectorization (delete existing embeddings first, then re-embed)
- Chunk-level progress via ``progress_callback`` on ``embed_document()``

.. note::
   Batch state is held **in memory**.  Container restart / scale-out will
   lose running batches.  This is acceptable for the current single‑node
   Docker Compose deployment; a persistent ``batch_vectorize`` table should
   be added when this becomes a production requirement.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import func, select

from app.core.config import settings
from app.db.models import Chunk, ChunkEmbedding, Document, KnowledgeBase
from app.db.session import SessionLocal
from app.services import embedding_service

logger = logging.getLogger("know_rag.batch_vectorize")

# ── in-memory batch registry ─────────────────────────────────────────────────
_batches: dict[str, "_BatchState"] = {}


@dataclass
class _DocItem:
    document_id: str
    title: str
    status: str = "pending"  # pending | running | completed | failed | skipped
    total_chunks: int = 0
    embedded_chunks: int = 0
    skipped_chunks: int = 0
    failed_chunks: int = 0
    percent: int = 0
    error: str = ""


@dataclass
class _BatchState:
    batch_id: str
    knowledge_base_id: str
    status: str = "queued"
    total_documents: int = 0
    completed_documents: int = 0
    failed_documents: int = 0
    skipped_documents: int = 0
    total_chunks: int = 0
    embedded_chunks: int = 0
    current_document_id: str = ""
    current_document_title: str = ""
    percent: int = 0
    items: list[_DocItem] = field(default_factory=list)
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0


def _iso(ts: float) -> str | None:
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _chunk_counts(db, doc_id: str) -> tuple[int, int]:
    """Return (total_eligible, already_embedded) for a document."""
    total = db.scalar(
        select(func.count())
        .select_from(Chunk)
        .where(
            Chunk.document_id == doc_id,
            Chunk.is_enabled.is_(True),
            Chunk.status == "ready",
            Chunk.chunk_type.in_(["text", "table", "image"]),
        )
    ) or 0
    embedded = db.scalar(
        select(func.count())
        .select_from(ChunkEmbedding)
        .join(Chunk, Chunk.id == ChunkEmbedding.chunk_id)
        .where(
            Chunk.document_id == doc_id,
            ChunkEmbedding.source_type == "chunk",
            ChunkEmbedding.is_enabled.is_(True),
        )
    ) or 0
    return total, embedded


def _collect_docs(
    kb_id: str,
    document_ids: list[str] | None = None,
    only_unvectorized: bool = False,
) -> list[Document]:
    """Return documents to process, respecting filter options."""
    db = SessionLocal()
    try:
        q = db.query(Document).filter(
            Document.knowledge_base_id == kb_id,
            Document.parse_status == "parsed",
        )
        if document_ids:
            q = q.filter(Document.id.in_(document_ids))
        docs = q.all()
        if only_unvectorized:
            docs = [
                d for d in docs
                if _chunk_counts(db, d.id)[0] > _chunk_counts(db, d.id)[1]
            ]
        return docs
    finally:
        db.close()


async def _run_batch(batch_id: str) -> None:
    state = _batches.get(batch_id)
    if state is None:
        return
    state.status = "running"
    state.started_at = time.time()

    try:
        await embedding_service.ensure_embedding_ready()
    except Exception as exc:
        state.status = "failed"
        state.error = f"Embedding 运行时不可用: {exc}"
        state.finished_at = time.time()
        return

    def _on_chunk_progress(info: dict) -> None:
        """Callback from embed_document — update chunk-level progress."""
        nonlocal state
        doc_id = info.get("document_id", "")
        embedded = info.get("embedded_chunks", 0)
        total = info.get("total_chunks", 0)
        # Update the matching item
        for item in state.items:
            if item.document_id == doc_id:
                item.embedded_chunks = embedded
                item.total_chunks = total
                item.percent = int(embedded / max(total, 1) * 100)
                break
        # Recompute global totals
        acc_total = 0
        acc_embedded = 0
        for it in state.items:
            acc_total += it.total_chunks
            acc_embedded += it.embedded_chunks
        state.total_chunks = acc_total
        state.embedded_chunks = acc_embedded
        state.percent = int(state.completed_documents / max(state.total_documents, 1) * 100)

    total_chunks_acc = 0
    embedded_chunks_acc = 0

    for item in state.items:
        if item.status in ("completed", "skipped"):
            total_chunks_acc += item.total_chunks
            embedded_chunks_acc += item.embedded_chunks
            continue

        state.current_document_id = item.document_id
        state.current_document_title = item.title
        item.status = "running"

        db = None
        try:
            db = SessionLocal()
            total, already = _chunk_counts(db, item.document_id)
            item.total_chunks = total
            item.embedded_chunks = already
            item.percent = int(already / max(total, 1) * 100) if total > 0 else 100

            if already >= total and total > 0:
                item.status = "skipped"
                item.embedded_chunks = total
                item.percent = 100
                total_chunks_acc += total
                embedded_chunks_acc += total
                state.skipped_documents += 1
                state.completed_documents += 1
                state.percent = int(state.completed_documents / max(state.total_documents, 1) * 100)
                continue

            result = await embedding_service.embed_document(
                db, None, item.document_id,
                progress_callback=_on_chunk_progress,
            )

            if result.get("success"):
                item.status = "completed"
                data = result.get("data", {})
                item.embedded_chunks = data.get("embedded_chunks", total)
                update_total, _ = _chunk_counts(db, item.document_id)
                item.total_chunks = update_total
                item.percent = 100
                total_chunks_acc += update_total
                embedded_chunks_acc += item.embedded_chunks
                state.completed_documents += 1
            else:
                item.status = "failed"
                item.error = result.get("message", "向量化失败")
                state.failed_documents += 1
        except Exception as exc:
            item.status = "failed"
            item.error = str(exc)[:300]
            state.failed_documents += 1
            logger.exception("batch_vectorize document=%s failed", item.document_id)
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

        state.total_chunks = total_chunks_acc + sum(
            it.total_chunks for it in state.items if it.status in ("running", "completed", "skipped")
        )
        state.embedded_chunks = embedded_chunks_acc + sum(
            it.embedded_chunks for it in state.items if it.status in ("running", "completed", "skipped")
        )
        state.percent = int(state.completed_documents / max(state.total_documents, 1) * 100)

    state.current_document_id = ""
    state.current_document_title = ""
    state.finished_at = time.time()

    if state.failed_documents == 0 and state.completed_documents > 0:
        state.status = "completed"
    elif state.completed_documents == 0:
        state.status = "failed"
    else:
        state.status = "partial_failed"
    state.percent = 100 if state.status == "completed" else state.percent

    logger.info(
        "batch_vectorize batch_id=%s status=%s completed=%d failed=%d chunks=%d/%d",
        batch_id,
        state.status,
        state.completed_documents,
        state.failed_documents,
        state.embedded_chunks,
        state.total_chunks,
    )


def start_batch_vectorize(
    kb_id: str,
    document_ids: list[str] | None = None,
    only_unvectorized: bool = True,
    force: bool = False,
) -> dict:
    """Create and launch a batch vectorize task.

    Args:
        kb_id: Knowledge base ID.
        document_ids: Optional subset of document IDs.  If empty, all
            parsed docs in the KB are used.
        only_unvectorized: When True, skip documents where every eligible
            chunk already has an embedding.
        force: When True, delete existing embeddings before re-embedding
            (not yet implemented — reserved).
    """
    db = SessionLocal()
    try:
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None:
            return {"success": False, "data": None, "message": f"知识库不存在：{kb_id}"}
        docs = _collect_docs(kb_id, document_ids, only_unvectorized)
        if not docs:
            return {
                "success": False,
                "data": None,
                "message": "该知识库没有符合条件的已解析文档",
            }
    finally:
        db.close()

    batch_id = uuid.uuid4().hex[:12]
    items: list[_DocItem] = []
    total_chunks_acc = 0
    embedded_chunks_acc = 0

    db2 = SessionLocal()
    try:
        for doc in docs:
            t, e = _chunk_counts(db2, doc.id)
            items.append(_DocItem(
                document_id=doc.id,
                title=doc.title,
                total_chunks=t,
                embedded_chunks=e,
                percent=int(e / max(t, 1) * 100) if t > 0 else 100,
            ))
            total_chunks_acc += t
            embedded_chunks_acc += e
    finally:
        db2.close()

    state = _BatchState(
        batch_id=batch_id,
        knowledge_base_id=kb_id,
        total_documents=len(items),
        total_chunks=total_chunks_acc,
        embedded_chunks=embedded_chunks_acc,
        items=items,
    )
    _batches[batch_id] = state

    asyncio.ensure_future(_run_batch(batch_id))

    return {
        "success": True,
        "data": {
            "batch_id": batch_id,
            "status": "running",
            "total_documents": len(items),
        },
        "message": "批量向量化任务已启动",
    }


def get_batch_status(batch_id: str) -> dict:
    """Return the current state of a batch vectorize task."""
    state = _batches.get(batch_id)
    if state is None:
        return {
            "success": False,
            "data": None,
            "message": f"批量任务不存在或已过期：{batch_id}",
        }
    return {
        "success": True,
        "data": {
            "batch_id": state.batch_id,
            "knowledge_base_id": state.knowledge_base_id,
            "status": state.status,
            "total_documents": state.total_documents,
            "completed_documents": state.completed_documents,
            "failed_documents": state.failed_documents,
            "skipped_documents": state.skipped_documents,
            "total_chunks": state.total_chunks,
            "embedded_chunks": state.embedded_chunks,
            "current_document_id": state.current_document_id,
            "current_document_title": state.current_document_title,
            "percent": state.percent,
            "error": state.error,
            "started_at": _iso(state.started_at),
            "finished_at": _iso(state.finished_at),
            "items": [
                {
                    "document_id": it.document_id,
                    "title": it.title,
                    "status": it.status,
                    "total_chunks": it.total_chunks,
                    "embedded_chunks": it.embedded_chunks,
                    "skipped_chunks": it.skipped_chunks,
                    "failed_chunks": it.failed_chunks,
                    "percent": it.percent,
                    "error": it.error,
                }
                for it in state.items
            ],
        },
    }

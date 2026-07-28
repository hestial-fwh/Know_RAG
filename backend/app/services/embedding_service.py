import logging
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.response import error_response, success_response
from app.db.models import Chunk, ChunkEmbedding, Document, KnowledgeBase, ModelConfig, User
from app.services.audit_service import record_audit
from app.services.content_cleaning import (
    clean_text_for_embedding,
    is_visual_ocr_quarantined,
    SKIP_REASON_VISUAL_OCR_QUARANTINE,
)
from app.services.embedding.embedding_content_builder import build_embedding_content
from app.services.vector_store_adapters import upsert_external_vector
from app.utils.ids import new_id


logger = logging.getLogger("know_rag.embedding")


@dataclass
class EmbeddingRuntime:
    provider: str
    model_name: str
    base_url: str
    api_key: str = ""
    dimension: int | None = None


def _fallback_embedding_runtime() -> EmbeddingRuntime:
    return EmbeddingRuntime(
        provider="ollama",
        model_name=settings.ollama_embedding_model,
        base_url=settings.ollama_base_url,
        api_key="",
        dimension=settings.ollama_embedding_dimension,
    )


def get_embedding_runtime(db: Session | None) -> EmbeddingRuntime:
    """Load embedding settings from model_config.

    Env values are only used as a bootstrap fallback when the database row does
    not exist yet. All normal embedding calls pass a DB session, so saved UI
    configuration is the source of truth.
    """

    if db is None:
        return _fallback_embedding_runtime()
    row = db.query(ModelConfig).filter(ModelConfig.model_type == "embedding").first()
    if row is None or not row.model_name or not row.base_url:
        from app.services.model_config_service import get_or_create_model_configs

        get_or_create_model_configs(db)
        row = db.query(ModelConfig).filter(ModelConfig.model_type == "embedding").first()
    if row is None or not row.model_name or not row.base_url:
        return _fallback_embedding_runtime()
    return EmbeddingRuntime(
        provider=(row.provider or "ollama").lower(),
        model_name=row.model_name,
        base_url=(row.base_url or "").rstrip("/"),
        api_key=row.api_key or "",
        dimension=row.dimension,
    )


def validate_dimension(vector: list[float], runtime: EmbeddingRuntime) -> None:
    expected = runtime.dimension
    if expected and len(vector) != expected:
        raise ValueError(f"Embedding 维度不一致：数据库配置为 {expected}，实际返回 {len(vector)}。请检查模型配置。")


def _stamp_skip_reason(chunk: Chunk, reason: str) -> None:
    """Stamp ``skip_embedding_recommended`` / ``skip_reason`` onto a chunk.

    Used for both empty-content skips and visual-OCR quarantine skips so
    downstream consumers (keyword index, content builder, audit queries) see
    a consistent signal.
    """
    meta = dict(chunk.metadata_json or {})
    meta.setdefault("skip_embedding_recommended", True)
    meta.setdefault("skip_reason", reason)
    flags = dict(chunk.flags or {})
    flags.setdefault("skip_embedding_recommended", True)
    flags.setdefault("skip_reason", meta.get("skip_reason") or reason)
    chunk.metadata_json = meta
    chunk.flags = flags


def _apply_visual_ocr_quarantine(chunk: Chunk) -> bool:
    """Stamp quarantine flags and return True if chunk should be skipped.

    Reads ``chunk_policy_image_ocr_mode`` from metadata.  When active, writes
    ``skip_embedding_recommended`` / ``skip_reason`` into both ``metadata_json``
    and ``flags`` so downstream consumers (keyword index, content builder, audit
    queries) see a consistent signal.

    Avoids overwriting already-stamped quarantine keys so re-embedding (retry,
    model change) does not trigger spurious SQLAlchemy UPDATEs for JSON columns.
    """
    meta = chunk.metadata_json or {}
    if not is_visual_ocr_quarantined(meta):
        return False

    flags = chunk.flags or {}
    # If both metadata and flags already carry the quarantine signal,
    # return early without mutating — avoids dirty-tracking on re-embed.
    if (
        meta.get("skip_embedding_recommended") is True
        and meta.get("skip_reason") == SKIP_REASON_VISUAL_OCR_QUARANTINE
        and flags.get("skip_embedding_recommended") is True
        and flags.get("skip_reason") == SKIP_REASON_VISUAL_OCR_QUARANTINE
    ):
        return True

    _stamp_skip_reason(chunk, SKIP_REASON_VISUAL_OCR_QUARANTINE)
    return True


async def _ollama_models(base_url: str) -> list[str]:
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get(f"{base_url}/api/tags")
    if not response.is_success:
        return []
    return [item.get("name", "") for item in response.json().get("models", [])]


async def ensure_embedding_ready(db: Session | None = None) -> EmbeddingRuntime:
    runtime = get_embedding_runtime(db)
    if runtime.provider in {"ollama", "local_ollama"}:
        names = await _ollama_models(runtime.base_url)
        if not names:
            raise RuntimeError(f"Ollama 服务不可用或没有模型：{runtime.base_url}")
        if runtime.model_name not in names and not any(name.startswith(f"{runtime.model_name}:") for name in names):
            raise RuntimeError(f"Embedding 模型不可用，请先执行：ollama pull {runtime.model_name}")
    return runtime




async def _embed_text_ollama(text: str, runtime: EmbeddingRuntime) -> list[float]:
    async with httpx.AsyncClient(timeout=120) as client:
        try:
            response = await client.post(
                f"{runtime.base_url}/api/embed",
                json={"model": runtime.model_name, "input": text},
            )
            if response.is_success:
                payload = response.json()
                embeddings = payload.get("embeddings") or []
                if embeddings and isinstance(embeddings[0], list):
                    return [float(item) for item in embeddings[0]]
        except httpx.RequestError as exc:
            raise RuntimeError(f"Ollama 服务不可用：{runtime.base_url}") from exc

        response = await client.post(
            f"{runtime.base_url}/api/embeddings",
            json={"model": runtime.model_name, "prompt": text},
        )
        if not response.is_success:
            raise RuntimeError("Ollama embedding 调用失败，请确认模型可用")
        payload = response.json()
        embedding = payload.get("embedding")
        if not isinstance(embedding, list):
            raise RuntimeError("Ollama embedding 返回格式不正确")
        return [float(item) for item in embedding]


async def _embed_text_openai_compatible(text: str, runtime: EmbeddingRuntime) -> list[float]:
    endpoint = runtime.base_url.rstrip("/")
    if not endpoint.endswith("/embeddings"):
        endpoint = f"{endpoint}/embeddings"

    headers = {"Content-Type": "application/json"}
    if runtime.api_key:
        headers["Authorization"] = f"Bearer {runtime.api_key}"

    payload = {
        "model": runtime.model_name,
        "input": text,
        "encoding_format": "float",
    }

    if runtime.dimension:
        payload["dimensions"] = int(runtime.dimension)

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(endpoint, headers=headers, json=payload)

    if not response.is_success:
        raise RuntimeError(f"Embedding API 调用失败：HTTP {response.status_code} {response.text[:500]}")

    data = response.json()
    embedding = None

    if isinstance(data.get("data"), list) and data["data"]:
        embedding = data["data"][0].get("embedding")

    if embedding is None:
        embedding = data.get("embedding")

    if not isinstance(embedding, list):
        raise RuntimeError(f"Embedding API 返回格式不正确：{str(data)[:500]}")

    return [float(item) for item in embedding]


async def embed_text(text: str, db: Session | None = None, check_model: bool = True) -> list[float]:
    clean_text = clean_text_for_embedding(text)
    if not clean_text.strip():
        raise ValueError("文本不能为空")
    runtime = await ensure_embedding_ready(db) if check_model else get_embedding_runtime(db)
    provider = runtime.provider.lower()
    if provider in {"ollama", "local_ollama"}:
        vector = await _embed_text_ollama(clean_text, runtime)
    elif provider in {
    "openai",
    "openai-compatible",
    "api",
    "custom",
    "dashscope",
    "siliconflow",
    "zhipu",
    "hunyuan",
    "volcengine",
    "deepseek",
    "xai",
    "stepfun",
}:
        vector = await _embed_text_openai_compatible(clean_text, runtime)
    else:
        raise RuntimeError(f"暂不支持的 embedding provider：{runtime.provider}")
    validate_dimension(vector, runtime)
    return vector


def embedding_to_vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(str(float(item)) for item in vector) + "]"


async def test_embedding(payload: dict, actor: User | None = None, db: Session | None = None):
    payload = payload or {}
    try:
        chunk = SimpleNamespace(
            content=payload.get("text") or "这是一个本地知识库向量测试",
            context_header=payload.get("context_header") or "",
        )
        document = SimpleNamespace(title=payload.get("title") or "", file_name="")
        input_text = build_embedding_content(chunk, document)
        vector = await embed_text(input_text, db=db)
        runtime = get_embedding_runtime(db)
        result = {
            "provider": runtime.provider,
            "model": runtime.model_name,
            "dimension": len(vector),
            "configured_dimension": runtime.dimension,
            "sample_size": len(vector),
            "input_length": len(input_text),
            "input_preview": input_text[:200],
        }
        if db is not None:
            record_audit(
                db,
                actor,
                "embedding_test",
                "embedding",
                "",
                request_summary={"input_length": len(input_text)},
                result_summary={"model": runtime.model_name, "dimension": len(vector)},
            )
            db.commit()
        return success_response(result)
    except Exception as exc:
        if db is not None:
            db.rollback()
        return error_response(str(exc) or "Embedding 测试失败")


def _eligible_chunks_query(db: Session, knowledge_base_id: str | None = None, document_id: str | None = None):
    query = db.query(Chunk).filter(
        Chunk.is_enabled.is_(True),
        Chunk.status == "ready",
        Chunk.chunk_type.in_(["text", "table", "image", "image_ocr_text", "equation"]),
        func.length(Chunk.content) > 0,
    )
    if knowledge_base_id:
        query = query.filter(Chunk.knowledge_base_id == knowledge_base_id)
    if document_id:
        query = query.filter(Chunk.document_id == document_id)
    return query.order_by(Chunk.document_id.asc(), Chunk.chunk_index.asc())


def _skip_embedding_expr():
    return Chunk.metadata_json.op("->>")("skip_embedding_recommended") == "true"


def _enabled_chunk_embedding_exists():
    return select(ChunkEmbedding.id).where(
        ChunkEmbedding.chunk_id == Chunk.id,
        ChunkEmbedding.source_type == "chunk",
        ChunkEmbedding.is_enabled.is_(True),
    ).exists()


def _count_missing_embeddings(db: Session, chunks: list[Chunk]) -> int:
    if not chunks:
        return 0
    chunk_ids = [chunk.id for chunk in chunks]
    embedded = (
        db.query(ChunkEmbedding.chunk_id)
        .filter(
            ChunkEmbedding.chunk_id.in_(chunk_ids),
            ChunkEmbedding.source_type == "chunk",
            ChunkEmbedding.is_enabled.is_(True),
        )
        .distinct()
        .count()
    )
    return max(len(chunk_ids) - embedded, 0)


def _upsert_embedding(db: Session, chunk: Chunk, document: Document, vector: list[float], index_content: str, runtime: EmbeddingRuntime) -> None:
    row = db.query(ChunkEmbedding).filter(ChunkEmbedding.chunk_id == chunk.id).first()
    if row is None:
        row = ChunkEmbedding(
            id=new_id(),
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            knowledge_base_id=chunk.knowledge_base_id,
        )
        db.add(row)
    row.document_id = chunk.document_id
    row.knowledge_id = chunk.knowledge_id or chunk.document_id
    row.knowledge_base_id = chunk.knowledge_base_id
    row.source_id = chunk.id
    row.source_type = "chunk"
    row.content = index_content
    row.is_enabled = bool(chunk.is_enabled)
    row.embedding = vector
    row.model_name = runtime.model_name
    row.dimension = len(vector)
    # Keep PostgreSQL/pgvector as source of truth and optionally mirror the
    # point into Qdrant/Milvus according to vector_store_config.
    upsert_external_vector(db, chunk, vector, index_content)


def _result_payload(
    *,
    runtime: EmbeddingRuntime,
    document_id: str | None = None,
    knowledge_base_id: str | None = None,
    total_chunks: int,
    embedded_chunks: int,
    skipped_chunks: int = 0,
    missing_chunks: int | None = None,
    pending_chunks: int | None = None,
    missing_chunks_before: int | None = None,
    embedded_this_run: int | None = None,
    skipped_this_run: int | None = None,
    failed_chunks: int = 0,
) -> dict:
    missing = max(total_chunks - embedded_chunks, 0) if missing_chunks is None else missing_chunks
    pending = max(missing - skipped_chunks, 0) if pending_chunks is None else max(pending_chunks, 0)
    payload = {
        "total_chunks": total_chunks,
        "embedded_chunks": embedded_chunks,
        "skipped_chunks": skipped_chunks,
        "missing_chunks": missing,
        "pending_chunks": pending,
        "real_missing_chunks": pending,
        "failed_chunks": failed_chunks,
        "provider": runtime.provider,
        "model": runtime.model_name,
        "dimension": runtime.dimension or 0,
    }
    if missing_chunks_before is not None:
        payload["missing_chunks_before"] = missing_chunks_before
    if embedded_this_run is not None:
        payload["embedded_this_run"] = embedded_this_run
    if skipped_this_run is not None:
        payload["skipped_this_run"] = skipped_this_run
    if document_id is not None:
        payload["document_id"] = document_id
    if knowledge_base_id is not None:
        payload["knowledge_base_id"] = knowledge_base_id
    return payload


async def embed_chunks(db: Session, actor: User | None, chunk_ids: list[str]):
    try:
        runtime = await ensure_embedding_ready(db)
        chunks = (
            db.query(Chunk)
            .filter(
                Chunk.id.in_(chunk_ids),
                Chunk.is_enabled.is_(True),
                Chunk.status == "ready",
                Chunk.chunk_type.in_(["text", "table", "image", "image_ocr_text", "equation"]),
            )
            .order_by(Chunk.document_id.asc(), Chunk.chunk_index.asc())
            .all()
        )
        documents = {doc.id: doc for doc in db.query(Document).filter(Document.id.in_({c.document_id for c in chunks})).all()}
        embedded = 0
        skipped = 0
        for chunk in chunks:
            document = documents.get(chunk.document_id)
            if _apply_visual_ocr_quarantine(chunk):
                skipped += 1
                continue
            index_content = build_embedding_content(chunk, document)
            if not index_content:
                skipped += 1
                _stamp_skip_reason(chunk, "embedding_content_empty")
                continue
            vector = await embed_text(index_content, db=db, check_model=False)
            _upsert_embedding(db, chunk, document, vector, index_content, runtime)
            embedded += 1
        missing = _count_missing_embeddings(db, chunks)
        db.commit()
        return success_response(
            _result_payload(runtime=runtime, total_chunks=len(chunks), embedded_chunks=embedded, skipped_chunks=skipped, missing_chunks=missing),
            "向量化完成",
        )
    except SQLAlchemyError:
        db.rollback()
        return error_response("chunk_embeddings 表不可用，请执行 alembic upgrade head")
    except Exception as exc:
        db.rollback()
        return error_response(str(exc) or "向量生成失败")


async def embed_document(
    db: Session,
    actor: User | None,
    document_id: str,
    progress_callback: Callable[[dict], None] | None = None,
):
    document = db.get(Document, document_id)
    if document is None:
        return error_response(f"文档不存在：{document_id}")
    if document.parse_status != "parsed":
        return error_response("文档尚未解析，无法生成向量")

    try:
        chunks = _eligible_chunks_query(db, document_id=document.id).all()
    except SQLAlchemyError:
        db.rollback()
        return error_response("chunk_embeddings 表不可用，请执行 alembic upgrade head")

    if not chunks:
        return error_response("该文档没有可用切片，请先解析文档")

    embedded = 0
    skipped = 0
    failed = 0
    total = len(chunks)
    missing_before = _count_missing_embeddings(db, chunks)
    try:
        runtime = await ensure_embedding_ready(db)
        for chunk in chunks:
            if _apply_visual_ocr_quarantine(chunk):
                skipped += 1
                if progress_callback:
                    progress_callback({
                        "document_id": document.id,
                        "chunk_id": chunk.id,
                        "embedded_chunks": embedded,
                        "total_chunks": total,
                        "skipped_chunks": skipped,
                        "failed_chunks": failed,
                    })
                continue
            index_content = build_embedding_content(chunk, document)
            if not index_content:
                skipped += 1
                _stamp_skip_reason(chunk, "embedding_content_empty")
                if progress_callback:
                    progress_callback({
                        "document_id": document.id,
                        "chunk_id": chunk.id,
                        "embedded_chunks": embedded,
                        "total_chunks": total,
                        "skipped_chunks": skipped,
                        "failed_chunks": failed,
                    })
                continue
            try:
                vector = await embed_text(index_content, db=db, check_model=False)
                _upsert_embedding(db, chunk, document, vector, index_content, runtime)
                embedded += 1
            except Exception:
                failed += 1
                logger.exception("embed_document chunk=%s failed", chunk.id)
            if progress_callback:
                progress_callback({
                    "document_id": document.id,
                    "chunk_id": chunk.id,
                    "embedded_chunks": embedded,
                    "total_chunks": total,
                    "skipped_chunks": skipped,
                    "failed_chunks": failed,
                })
        missing = _count_missing_embeddings(db, chunks)
        record_audit(
            db,
            actor,
            "embed_document",
            "document",
            document.id,
            result_summary={
                "document_id": document.id,
                "knowledge_base_id": document.knowledge_base_id,
                "chunk_count": len(chunks),
                "embedded_chunks": embedded,
                "model": runtime.model_name,
                "dimension": runtime.dimension,
            },
        )
        db.commit()
        logger.info(
            "embed_document document_id=%s total_chunks=%s embedded_chunks=%s provider=%s model=%s dimension=%s",
            document.id,
            len(chunks),
            embedded,
            runtime.provider,
            runtime.model_name,
            runtime.dimension,
        )
        return success_response(
            _result_payload(
                runtime=runtime,
                document_id=document.id,
                total_chunks=len(chunks),
                embedded_chunks=(total - missing),
                skipped_chunks=skipped,
                missing_chunks=missing,
                pending_chunks=max(missing - skipped, 0),
                missing_chunks_before=missing_before,
                embedded_this_run=embedded,
                skipped_this_run=skipped,
                failed_chunks=failed,
            ),
            "文档向量化完成",
        )
    except SQLAlchemyError:
        db.rollback()
        return error_response("chunk_embeddings 表不可用，请执行 alembic upgrade head")
    except Exception as exc:
        db.rollback()
        return error_response(str(exc) or "向量生成失败")


async def embed_knowledge_base(db: Session, actor: User | None, kb_id: str):
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        return error_response(f"知识库不存在：{kb_id}")
    try:
        chunks = (
            _eligible_chunks_query(db, knowledge_base_id=kb_id)
            .join(Document, Document.id == Chunk.document_id)
            .filter(Document.parse_status == "parsed")
            .all()
        )
    except SQLAlchemyError:
        db.rollback()
        return error_response("chunk_embeddings 表不可用，请执行 alembic upgrade head")

    if not chunks:
        return error_response("知识库没有可用切片")

    embedded = 0
    skipped = 0
    try:
        runtime = await ensure_embedding_ready(db)
        document_ids = {chunk.document_id for chunk in chunks}
        documents = {doc.id: doc for doc in db.query(Document).filter(Document.id.in_(document_ids)).all()}
        for chunk in chunks:
            document = documents.get(chunk.document_id)
            if _apply_visual_ocr_quarantine(chunk):
                skipped += 1
                continue
            index_content = build_embedding_content(chunk, document)
            if not index_content:
                skipped += 1
                _stamp_skip_reason(chunk, "embedding_content_empty")
                continue
            vector = await embed_text(index_content, db=db, check_model=False)
            _upsert_embedding(db, chunk, document, vector, index_content, runtime)
            embedded += 1
        missing = _count_missing_embeddings(db, chunks)
        record_audit(
            db,
            actor,
            "embed_knowledge_base",
            "knowledge_base",
            kb_id,
            result_summary={
                "knowledge_base_id": kb_id,
                "chunk_count": len(chunks),
                "embedded_chunks": embedded,
                "model": runtime.model_name,
                "dimension": runtime.dimension,
            },
        )
        db.commit()
        logger.info(
            "embed_knowledge_base knowledge_base_id=%s total_chunks=%s embedded_chunks=%s provider=%s model=%s dimension=%s",
            kb_id,
            len(chunks),
            embedded,
            runtime.provider,
            runtime.model_name,
            runtime.dimension,
        )
        return success_response(
            _result_payload(
                runtime=runtime,
                knowledge_base_id=kb_id,
                total_chunks=len(chunks),
                embedded_chunks=embedded,
                skipped_chunks=skipped,
                missing_chunks=missing,
            ),
            "知识库向量化完成",
        )
    except SQLAlchemyError:
        db.rollback()
        return error_response("chunk_embeddings 表不可用，请执行 alembic upgrade head")
    except Exception as exc:
        db.rollback()
        return error_response(str(exc) or "向量生成失败")


def _status_payload(db: Session, filters: list, identity: dict) -> dict:
    runtime = get_embedding_runtime(db)
    eligible_filters = [
        *filters,
        Chunk.is_enabled.is_(True),
        Chunk.status == "ready",
        Chunk.chunk_type.in_(["text", "table", "image", "image_ocr_text", "equation"]),
    ]
    total = db.scalar(
        select(func.count())
        .select_from(Chunk)
        .where(*eligible_filters)
    ) or 0

    embedded_exists = _enabled_chunk_embedding_exists()
    skip_expr = _skip_embedding_expr()

    embedded = db.scalar(
        select(func.count())
        .select_from(Chunk)
        .where(*eligible_filters, embedded_exists)
    ) or 0
    skipped = db.scalar(
        select(func.count())
        .select_from(Chunk)
        .where(*eligible_filters, ~embedded_exists, skip_expr)
    ) or 0
    pending = db.scalar(
        select(func.count())
        .select_from(Chunk)
        .where(*eligible_filters, ~embedded_exists, ~skip_expr)
    ) or 0
    has_context_header_count = db.scalar(
        select(func.count())
        .select_from(Chunk)
        .where(*eligible_filters, func.length(Chunk.context_header) > 0)
    ) or 0
    indexed_content_count = db.scalar(
        select(func.count())
        .select_from(ChunkEmbedding)
        .join(Chunk, Chunk.id == ChunkEmbedding.chunk_id)
        .where(
            *filters,
            Chunk.is_enabled.is_(True),
            Chunk.status == "ready",
            Chunk.chunk_type.in_(["text", "table", "image", "image_ocr_text", "equation"]),
            ChunkEmbedding.source_type == "chunk",
            ChunkEmbedding.is_enabled.is_(True),
            func.length(ChunkEmbedding.content) > 0,
        )
    ) or 0
    missing = max(total - embedded, 0)
    return {
        **identity,
        "total_chunks": total,
        "embedded_chunks": embedded,
        "missing_chunks": missing,
        "skipped_chunks": skipped,
        "pending_chunks": pending,
        "real_missing_chunks": pending,
        "has_context_header_count": has_context_header_count,
        "indexed_content_count": indexed_content_count,
        "provider": runtime.provider,
        "model": runtime.model_name,
        "dimension": runtime.dimension or 0,
    }

def document_embedding_status(db: Session, document_id: str):
    document = db.get(Document, document_id)
    if document is None:
        return error_response(f"文档不存在：{document_id}")
    try:
        payload = _status_payload(db, [Chunk.document_id == document_id], {"document_id": document_id})
    except SQLAlchemyError:
        db.rollback()
        return error_response("chunk_embeddings 表不可用，请执行 alembic upgrade head")
    return success_response(payload)


def knowledge_base_embedding_status(db: Session, kb_id: str):
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        return error_response(f"知识库不存在：{kb_id}")
    try:
        payload = _status_payload(db, [Chunk.knowledge_base_id == kb_id], {"knowledge_base_id": kb_id})
    except SQLAlchemyError:
        db.rollback()
        return error_response("chunk_embeddings 表不可用，请执行 alembic upgrade head")
    return success_response(payload)

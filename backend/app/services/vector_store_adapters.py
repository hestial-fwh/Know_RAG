"""Vector-store adapter layer.

Postgres/pgvector remains the local source of truth.  Qdrant and Milvus are
optional external indexes: embedding generation writes both to chunk_embeddings
and, when configured, to the active external vector database.  Retrieval reads
from the active provider and falls back to pgvector if configured to do so.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Chunk, ModelConfig, VectorStoreConfig
from app.utils.text import sanitize_text_for_postgres

logger = logging.getLogger(__name__)


@dataclass
class VectorHit:
    chunk_id: str
    score: float


def default_config_for_provider(provider: str) -> dict:
    provider = (provider or "postgres").lower()
    if provider in {"postgres", "pgvector"}:
        return {"database_url": settings.database_url, "fallback_to_postgres": True}
    if provider == "qdrant":
        return {
            "url": "http://qdrant:6333",
            "api_key": "",
            "collection": "know_rag_chunks",
            "distance": "Cosine",
            "fallback_to_postgres": True,
            "strict_external_index": False,
        }
    if provider in {"milvus", "zilliz"}:
        return {
            "uri": "http://milvus:19530",
            "token": "",
            "collection": "know_rag_chunks",
            "metric_type": "COSINE",
            "fallback_to_postgres": True,
            "strict_external_index": False,
        }
    return {"fallback_to_postgres": True, "strict_external_index": False}


def get_active_vector_config(db: Session) -> VectorStoreConfig | None:
    return db.query(VectorStoreConfig).filter(VectorStoreConfig.is_active.is_(True)).first()


def is_external_provider(config: VectorStoreConfig | None) -> bool:
    provider = ((config.provider if config else "") or "postgres").lower()
    return provider not in {"", "postgres", "pgvector"}


def _collection_name(config: VectorStoreConfig, default: str = "know_rag_chunks") -> str:
    cfg = config.config or {}
    return str(cfg.get("collection") or cfg.get("collection_name") or default)


def _embedding_dimension(db: Session, vector: list[float]) -> int:
    row = db.query(ModelConfig).filter(ModelConfig.model_type == "embedding").first()
    return int(row.dimension or len(vector)) if row else len(vector)


def _qdrant_client(config: VectorStoreConfig):
    try:
        from qdrant_client import QdrantClient
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("qdrant-client 未安装，请执行 pip install qdrant-client 或重新构建镜像") from exc
    cfg = config.config or {}
    url = cfg.get("url") or cfg.get("host") or "http://qdrant:6333"
    api_key = cfg.get("api_key") or None
    return QdrantClient(url=url, api_key=api_key)


def _qdrant_ensure_collection(client: Any, config: VectorStoreConfig, dimension: int):
    from qdrant_client.models import Distance, VectorParams

    collection = _collection_name(config)
    existing = {item.name for item in client.get_collections().collections}
    if collection in existing:
        return collection
    distance_name = str((config.config or {}).get("distance") or "Cosine").upper()
    distance = getattr(Distance, distance_name, Distance.COSINE)
    client.create_collection(collection_name=collection, vectors_config=VectorParams(size=dimension, distance=distance))
    return collection


def _qdrant_upsert(db: Session, config: VectorStoreConfig, chunk: Chunk, vector: list[float], content: str):
    from qdrant_client.models import PointStruct

    client = _qdrant_client(config)
    collection = _qdrant_ensure_collection(client, config, _embedding_dimension(db, vector))
    payload = {
        "chunk_id": chunk.id,
        "document_id": chunk.document_id,
        "knowledge_base_id": chunk.knowledge_base_id,
        "chunk_type": chunk.chunk_type,
        "chunk_index": chunk.chunk_index,
        "content": sanitize_text_for_postgres(content)[:6000],
    }
    client.upsert(collection_name=collection, points=[PointStruct(id=chunk.id, vector=vector, payload=payload)])


def _qdrant_delete(config: VectorStoreConfig, chunk_id: str) -> None:
    client = _qdrant_client(config)
    client.delete(
        collection_name=_collection_name(config),
        points_selector=[chunk_id],
        wait=True,
    )


def _qdrant_search(config: VectorStoreConfig, kb_id: str, query_vector: list[float], top_k: int) -> list[VectorHit]:
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    client = _qdrant_client(config)
    collection = _collection_name(config)
    result = client.search(
        collection_name=collection,
        query_vector=query_vector,
        query_filter=Filter(must=[FieldCondition(key="knowledge_base_id", match=MatchValue(value=kb_id))]),
        limit=top_k,
        with_payload=True,
    )
    hits: list[VectorHit] = []
    for item in result:
        payload = item.payload or {}
        chunk_id = str(payload.get("chunk_id") or item.id)
        hits.append(VectorHit(chunk_id=chunk_id, score=float(item.score or 0)))
    return hits


def _milvus_client(config: VectorStoreConfig):
    try:
        from pymilvus import MilvusClient
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("pymilvus 未安装，请执行 pip install pymilvus 或重新构建镜像") from exc
    cfg = config.config or {}
    return MilvusClient(uri=cfg.get("uri") or "http://milvus:19530", token=cfg.get("token") or "")


def _milvus_ensure_collection(client: Any, config: VectorStoreConfig, dimension: int):
    collection = _collection_name(config)
    if client.has_collection(collection):
        return collection
    from pymilvus import DataType

    metric_type = str((config.config or {}).get("metric_type") or "COSINE").upper()
    schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
    schema.add_field(field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=128)
    schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dimension)
    schema.add_field(field_name="chunk_id", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="document_id", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="knowledge_base_id", datatype=DataType.VARCHAR, max_length=128)
    index_params = client.prepare_index_params()
    index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type=metric_type)
    client.create_collection(collection_name=collection, schema=schema, index_params=index_params)
    return collection


def _milvus_upsert(db: Session, config: VectorStoreConfig, chunk: Chunk, vector: list[float], content: str):
    client = _milvus_client(config)
    collection = _milvus_ensure_collection(client, config, _embedding_dimension(db, vector))
    data = [{
        "id": chunk.id,
        "vector": vector,
        "chunk_id": chunk.id,
        "document_id": chunk.document_id,
        "knowledge_base_id": chunk.knowledge_base_id,
        "chunk_type": chunk.chunk_type,
        "chunk_index": int(chunk.chunk_index),
        "content": sanitize_text_for_postgres(content)[:6000],
    }]
    client.upsert(collection_name=collection, data=data)


def _milvus_delete(config: VectorStoreConfig, chunk_id: str) -> None:
    client = _milvus_client(config)
    client.delete(
        collection_name=_collection_name(config),
        ids=[chunk_id],
    )


def _milvus_search(config: VectorStoreConfig, kb_id: str, query_vector: list[float], top_k: int) -> list[VectorHit]:
    client = _milvus_client(config)
    collection = _collection_name(config)
    result = client.search(
        collection_name=collection,
        data=[query_vector],
        limit=top_k,
        filter=f'knowledge_base_id == "{kb_id}"',
        output_fields=["chunk_id"],
    )
    hits: list[VectorHit] = []
    for item in (result[0] if result else []):
        entity = item.get("entity") or {}
        chunk_id = str(entity.get("chunk_id") or item.get("id"))
        distance = float(item.get("distance") or item.get("score") or 0)
        score = distance if distance <= 1 else 1 / (1 + distance)
        hits.append(VectorHit(chunk_id=chunk_id, score=score))
    return hits


def upsert_external_vector(db: Session, chunk: Chunk, vector: list[float], content: str) -> None:
    config = get_active_vector_config(db)
    if not is_external_provider(config):
        return
    provider = (config.provider or "").lower()
    strict = bool((config.config or {}).get("strict_external_index", False))
    try:
        if provider == "qdrant":
            _qdrant_upsert(db, config, chunk, vector, content)
        elif provider in {"milvus", "zilliz"}:
            _milvus_upsert(db, config, chunk, vector, content)
        else:
            logger.info("Unsupported external vector provider=%s; skip upsert", provider)
    except Exception as exc:
        logger.warning("External vector upsert failed provider=%s chunk_id=%s: %s", provider, chunk.id, exc)
        if strict:
            raise


def delete_external_vector(db: Session, chunk_id: str) -> None:
    """Best-effort removal from the active external vector index."""

    config = get_active_vector_config(db)
    if not is_external_provider(config):
        return
    provider = (config.provider or "").lower()
    strict = bool((config.config or {}).get("strict_external_index", False))
    try:
        if provider == "qdrant":
            _qdrant_delete(config, chunk_id)
        elif provider in {"milvus", "zilliz"}:
            _milvus_delete(config, chunk_id)
        else:
            logger.info(
                "Unsupported external vector provider=%s; skip delete",
                provider,
            )
    except Exception as exc:
        logger.warning(
            "External vector delete failed provider=%s chunk_id=%s: %s",
            provider,
            chunk_id,
            exc,
        )
        if strict:
            raise


def search_external_vector(db: Session, kb_id: str, query_vector: list[float], top_k: int) -> tuple[list[VectorHit], str]:
    config = get_active_vector_config(db)
    if not is_external_provider(config):
        return [], ""
    provider = (config.provider or "").lower()
    try:
        if provider == "qdrant":
            return _qdrant_search(config, kb_id, query_vector, top_k), ""
        if provider in {"milvus", "zilliz"}:
            return _milvus_search(config, kb_id, query_vector, top_k), ""
        return [], f"暂不支持的外部向量库：{provider}"
    except Exception as exc:
        if bool((config.config or {}).get("fallback_to_postgres", True)):
            return [], f"外部向量库 {provider} 检索失败，已回退 pgvector：{exc}"
        raise

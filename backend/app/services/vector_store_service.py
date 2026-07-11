from sqlalchemy import text

from app.core.config import settings
from app.core.response import error_response, success_response
from app.db.models import VectorStoreConfig
from app.db.session import engine
from app.services.vector_store_adapters import default_config_for_provider
from app.utils.ids import new_id

PROVIDERS = [
    {"value": "postgres", "label": "PostgreSQL / pgvector", "default_config": default_config_for_provider("postgres")},
    {"value": "qdrant", "label": "Qdrant", "default_config": default_config_for_provider("qdrant")},
    {"value": "milvus", "label": "Milvus / Zilliz", "default_config": default_config_for_provider("milvus")},
]


def vector_store_to_dict(row: VectorStoreConfig) -> dict:
    return {
        "id": row.id,
        "provider": row.provider,
        "config": row.config,
        "is_active": row.is_active,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "providers": PROVIDERS,
    }


def get_or_create_config(db):
    row = db.query(VectorStoreConfig).filter(VectorStoreConfig.is_active.is_(True)).first()
    if row is not None:
        return row
    row = VectorStoreConfig(
        id=new_id(),
        provider="postgres",
        config=default_config_for_provider("postgres"),
        is_active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_config(db):
    return success_response(vector_store_to_dict(get_or_create_config(db)))


def provider_presets():
    return success_response({"providers": PROVIDERS})


def update_config(db, payload: dict):
    provider = (payload.get("provider") or "postgres").lower()
    if provider not in {"postgres", "pgvector", "qdrant", "milvus", "zilliz"}:
        return error_response("向量库供应商必须是 postgres、qdrant 或 milvus")
    row = get_or_create_config(db)
    row.provider = "postgres" if provider == "pgvector" else provider
    default_config = default_config_for_provider(row.provider)
    row.config = {**default_config, **(payload.get("config") or {})}
    row.is_active = bool(payload.get("is_active", True))
    db.commit()
    db.refresh(row)
    return success_response(vector_store_to_dict(row), "vector store config updated")


def test_vector_store(payload: dict | None = None):
    payload = payload or {}
    provider = (payload.get("provider") or "postgres").lower()
    cfg = {**default_config_for_provider(provider), **(payload.get("config") or {})}
    if provider in {"postgres", "pgvector"}:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return success_response({"available": True, "provider": "postgres"})
        except Exception as exc:
            return error_response(f"postgres connection failed: {exc}")
    if provider == "qdrant":
        try:
            from qdrant_client import QdrantClient
            client = QdrantClient(url=cfg.get("url") or "http://qdrant:6333", api_key=cfg.get("api_key") or None)
            client.get_collections()
            return success_response({"available": True, "provider": "qdrant", "url": cfg.get("url")})
        except Exception as exc:
            return error_response(f"qdrant connection failed: {exc}")
    if provider in {"milvus", "zilliz"}:
        try:
            from pymilvus import MilvusClient
            client = MilvusClient(uri=cfg.get("uri") or "http://milvus:19530", token=cfg.get("token") or "")
            client.list_collections()
            return success_response({"available": True, "provider": provider, "uri": cfg.get("uri")})
        except Exception as exc:
            return error_response(f"milvus connection failed: {exc}")
    return error_response(f"暂不支持的向量库供应商：{provider}")

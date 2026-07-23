from dataclasses import dataclass
from html import unescape
import logging
from math import log1p
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.response import error_response, success_response
from app.db.models import KnowledgeBase, RetrievalConfig, User
from app.services.audit_service import record_audit
from app.services.embedding_service import embed_text, embedding_to_vector_literal, get_embedding_runtime
from app.services.document_relation_expansion_service import expand_figure_bindings
from app.services.keyword_index_service import ElasticsearchQueryComplexityError
from app.services.vector_store_adapters import get_active_vector_config, is_external_provider, search_external_vector
from app.utils.ids import new_id


logger = logging.getLogger(__name__)


DEFAULT_RETRIEVAL_CONFIG = {
    "embedding_top_k": 5,
    "score_threshold": 0.3,
    "use_vector": True,
    "use_keyword": True,
    "rerank_model_id": "",
    "fusion_strategy": "rrf",
    "rrf_k": 60,
    "rrf_vector_weight": 1.0,
    "rrf_keyword_weight": 1.0,
    "include_context": True,
}

_PARENT_CONTEXT_MODE_DEFAULTS = {
    "compact": {"parent_context_max_chars": 500, "neighbor_context_max_chars": 150, "parent_children_limit": 5, "max_parent_contexts": 3},
    "standard": {"parent_context_max_chars": None, "neighbor_context_max_chars": None, "parent_children_limit": None, "max_parent_contexts": None},
    "full": {"parent_context_max_chars": 4000, "neighbor_context_max_chars": 500, "parent_children_limit": 30, "max_parent_contexts": 8},
}


@dataclass
class SearchOptions:
    query: str
    top_k: int
    vector_threshold: float
    keyword_threshold: float
    use_vector: bool
    use_keyword: bool
    include_content: bool
    include_context: bool
    include_parent_context: bool = True
    parent_context_mode: str = "standard"
    parent_context_max_chars: int = 0
    neighbor_context_max_chars: int = 0
    parent_children_limit: int = 0
    max_parent_contexts: int = 0
    merge_hits_by_parent: bool = False
    fusion_strategy: str = "rrf"
    rrf_k: int = 60
    vector_weight: float = 1.0
    keyword_weight: float = 1.0


def retrieval_config_to_dict(config: RetrievalConfig) -> dict:
    return {
        "id": config.id,
        "embedding_top_k": config.embedding_top_k,
        "score_threshold": config.score_threshold,
        "use_vector": config.use_vector,
        "use_keyword": config.use_keyword,
        "rerank_model_id": config.rerank_model_id,
        "fusion_strategy": config.fusion_strategy,
        "rrf_k": config.rrf_k,
        "rrf_vector_weight": config.rrf_vector_weight,
        "rrf_keyword_weight": config.rrf_keyword_weight,
        "include_context": config.include_context,
        "created_at": config.created_at,
        "updated_at": config.updated_at,
    }


def get_or_create_retrieval_config(db):
    config = db.query(RetrievalConfig).first()
    if config is not None:
        return config
    config = RetrievalConfig(id=new_id(), **DEFAULT_RETRIEVAL_CONFIG)
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def get_retrieval_config(db):
    return success_response(retrieval_config_to_dict(get_or_create_retrieval_config(db)))


def update_retrieval_config(db, payload: dict):
    config = get_or_create_retrieval_config(db)
    for key in DEFAULT_RETRIEVAL_CONFIG:
        if key in payload:
            setattr(config, key, payload[key])
    if config.fusion_strategy not in {"rrf", "weighted"}:
        return error_response("融合策略必须是 rrf 或 weighted")
    if config.rrf_k <= 0:
        return error_response("RRF 参数不合法")
    if config.rrf_vector_weight <= 0 or config.rrf_keyword_weight <= 0:
        return error_response("检索权重参数不合法")
    db.commit()
    db.refresh(config)
    return success_response(retrieval_config_to_dict(config), "保存成功")


def _bool_payload(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int_payload(value: Any, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        parsed = int(value) if value is not None and value != "" else int(default)
    except Exception:
        parsed = int(default)
    parsed = max(parsed, minimum)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def _float_payload(value: Any, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        parsed = float(value) if value is not None and value != "" else float(default)
    except Exception:
        parsed = float(default)
    if minimum is not None:
        parsed = max(parsed, minimum)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def _parent_context_defaults(mode: str) -> dict[str, int | None]:
    normalized = (mode or "standard").strip().lower()
    if normalized not in _PARENT_CONTEXT_MODE_DEFAULTS:
        normalized = "standard"
    defaults = dict(_PARENT_CONTEXT_MODE_DEFAULTS[normalized])
    if defaults.get("parent_context_max_chars") is None:
        defaults["parent_context_max_chars"] = getattr(settings, "retrieval_parent_context_max_chars", 2000)
    if defaults.get("neighbor_context_max_chars") is None:
        defaults["neighbor_context_max_chars"] = getattr(settings, "retrieval_neighbor_context_max_chars", 300)
    if defaults.get("parent_children_limit") is None:
        defaults["parent_children_limit"] = getattr(settings, "retrieval_parent_children_limit", 30)
    if defaults.get("max_parent_contexts") is None:
        defaults["max_parent_contexts"] = getattr(settings, "retrieval_max_parent_contexts", 5)
    return defaults


def normalize_options(db, payload: dict) -> tuple[SearchOptions | None, str | None]:
    config = get_or_create_retrieval_config(db)
    query = (payload.get("query_text") or payload.get("query") or "").strip()
    if not query:
        return None, "请输入检索内容"

    raw_top_k = payload.get("match_count") or payload.get("top_k") or config.embedding_top_k or settings.default_embedding_top_k
    try:
        top_k = int(raw_top_k)
    except (TypeError, ValueError):
        return None, "返回数量参数不合法"
    if top_k <= 0 or top_k > 100:
        return None, "返回数量参数不合法"

    default_threshold = payload.get("score_threshold")
    if default_threshold is None:
        default_threshold = config.score_threshold

    # Pydantic/FastAPI model_dump() may include optional fields with value None.
    # dict.get("vector_threshold", default_threshold) does not use the default
    # when the key exists but its value is None, so fall back explicitly.
    vector_threshold = payload.get("vector_threshold")
    if vector_threshold is None:
        vector_threshold = default_threshold

    keyword_threshold = payload.get("keyword_threshold")
    if keyword_threshold is None:
        keyword_threshold = default_threshold

    try:
        vector_threshold = float(vector_threshold)
        keyword_threshold = float(keyword_threshold)
    except (TypeError, ValueError):
        return None, "分数阈值不合法"
    if not 0 <= vector_threshold <= 1 or not 0 <= keyword_threshold <= 1:
        return None, "分数阈值不合法"

    disable_vector = _bool_payload(payload.get("disable_vector_match"))
    disable_keyword = _bool_payload(payload.get("disable_keywords_match"))
    use_vector_value = _bool_payload(payload.get("use_vector"))
    use_keyword_value = _bool_payload(payload.get("use_keyword"))
    use_vector = config.use_vector if use_vector_value is None else use_vector_value
    use_keyword = config.use_keyword if use_keyword_value is None else use_keyword_value
    if disable_vector is not None:
        use_vector = not disable_vector
    if disable_keyword is not None:
        use_keyword = not disable_keyword
    if not use_vector and not use_keyword:
        return None, "请至少启用一种检索方式"

    include_context_value = _bool_payload(payload.get("include_context"))
    include_context = config.include_context if include_context_value is None else include_context_value

    include_parent_context_value = _bool_payload(payload.get("include_parent_context"))
    include_parent_context = include_context if include_parent_context_value is None else include_parent_context_value

    parent_context_mode = (payload.get("parent_context_mode") or getattr(settings, "retrieval_parent_context_mode", "standard") or "standard").strip().lower()
    if parent_context_mode not in _PARENT_CONTEXT_MODE_DEFAULTS:
        parent_context_mode = "standard"
    parent_defaults = _parent_context_defaults(parent_context_mode)
    parent_context_max_chars = _int_payload(
        payload.get("parent_context_max_chars"),
        int(parent_defaults["parent_context_max_chars"] or 2000),
        minimum=0,
        maximum=50000,
    )
    neighbor_context_max_chars = _int_payload(
        payload.get("neighbor_context_max_chars"),
        int(parent_defaults["neighbor_context_max_chars"] or 300),
        minimum=0,
        maximum=20000,
    )
    parent_children_limit = _int_payload(
        payload.get("parent_children_limit"),
        int(parent_defaults["parent_children_limit"] or 30),
        minimum=0,
        maximum=500,
    )
    max_parent_contexts = _int_payload(
        payload.get("max_parent_contexts"),
        int(parent_defaults["max_parent_contexts"] or 5),
        minimum=0,
        maximum=100,
    )
    merge_hits_value = _bool_payload(payload.get("merge_hits_by_parent"))
    merge_hits_by_parent = bool(getattr(settings, "retrieval_merge_hits_by_parent", True)) if merge_hits_value is None else merge_hits_value

    fusion_strategy = (payload.get("fusion_strategy") or config.fusion_strategy or settings.hybrid_fusion_strategy).lower()
    if fusion_strategy not in {"rrf", "weighted"}:
        return None, "融合策略必须是 rrf 或 weighted"
    try:
        rrf_k = int(payload.get("rrf_k") or config.rrf_k or settings.rrf_k)
    except (TypeError, ValueError):
        return None, "RRF 参数不合法"
    if rrf_k <= 0:
        return None, "RRF 参数不合法"
    try:
        vector_weight = float(payload.get("rrf_vector_weight") or config.rrf_vector_weight or settings.rrf_vector_weight)
        keyword_weight = float(payload.get("rrf_keyword_weight") or config.rrf_keyword_weight or settings.rrf_keyword_weight)
    except (TypeError, ValueError):
        return None, "检索权重参数不合法"
    if vector_weight <= 0 or keyword_weight <= 0:
        return None, "检索权重参数不合法"

    return SearchOptions(
        query=query,
        top_k=top_k,
        vector_threshold=vector_threshold,
        keyword_threshold=keyword_threshold,
        use_vector=use_vector,
        use_keyword=use_keyword,
        include_content=bool(payload.get("include_content", True)),
        include_context=include_context,
        include_parent_context=include_parent_context,
        parent_context_mode=parent_context_mode,
        parent_context_max_chars=parent_context_max_chars,
        neighbor_context_max_chars=neighbor_context_max_chars,
        parent_children_limit=parent_children_limit,
        max_parent_contexts=max_parent_contexts,
        merge_hits_by_parent=merge_hits_by_parent,
        fusion_strategy=fusion_strategy,
        rrf_k=rrf_k,
        vector_weight=vector_weight,
        keyword_weight=keyword_weight,
    ), None


def _row_to_result(row, match_type: str, include_content: bool, score_key: str) -> dict:
    content = row.content or ""
    matched_content = (getattr(row, "matched_content", None) or content or "")[:1000]
    metadata = row.metadata_json or {}
    image_info = getattr(row, "image_info", None) or {}
    display_content = _clean_display_text(matched_content)
    result = {
        "id": row.chunk_id,
        "chunk_id": row.chunk_id,
        "document_id": row.document_id,
        "knowledge_id": row.knowledge_id or row.document_id,
        "knowledge_base_id": row.knowledge_base_id,
        "knowledge_title": row.title,
        "title": row.title,
        "file_name": row.file_name,
        "knowledge_filename": row.file_name,
        "file_hash": getattr(row, "file_hash", "") or "",
        "knowledge_source": getattr(row, "knowledge_source", "") or "",
        "knowledge_channel": getattr(row, "knowledge_channel", "") or "",
        "parse_status": row.parse_status,
        "matched_content": matched_content,
        "display_content": display_content,
        "display_preview": display_content[:300],
        "chunk_index": row.chunk_index,
        "seq": row.chunk_index,
        "start_at": row.start_at,
        "end_at": row.end_at,
        "pre_chunk_id": row.pre_chunk_id,
        "next_chunk_id": row.next_chunk_id,
        "chunk_type": row.chunk_type,
        "parent_chunk_id": row.parent_chunk_id,
        "context_header": row.context_header or "",
        "content_hash": row.content_hash or "",
        "metadata": metadata,
        "metadata_json": metadata,
        "chunk_metadata": metadata,
        "image_info": image_info,
        "image_url": _image_url_from_info(image_info, metadata),
        "preview": content[:300],
        "score": float(getattr(row, score_key) or 0),
        "rrf_score": 0.0,
        "vector_score": 0.0,
        "keyword_score": 0.0,
        "vector_rank": None,
        "keyword_rank": None,
        "match_type": match_type,
    }
    if include_content:
        result["content"] = content
    return result


def _base_select_sql(score_sql: str, extra_from: str = "", where_extra: str = "") -> str:
    return f"""
        SELECT
            c.id AS chunk_id,
            c.document_id,
            c.knowledge_id,
            c.knowledge_base_id,
            d.title,
            d.file_name,
            d.file_hash,
            d.source AS knowledge_source,
            d.channel AS knowledge_channel,
            d.parse_status,
            c.content,
            COALESCE(NULLIF(ce.content, ''), c.content) AS matched_content,
            c.chunk_index,
            c.start_at,
            c.end_at,
            c.pre_chunk_id,
            c.next_chunk_id,
            c.chunk_type,
            c.parent_chunk_id,
            c.context_header,
            c.content_hash,
            c.image_info,
            c.metadata_json,
            {score_sql}
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        {extra_from}
        WHERE c.knowledge_base_id = :kb_id
          AND c.is_enabled = true
          AND c.status = 'ready'
          AND c.chunk_type IN ('text', 'table', 'image', 'image_ocr_text', 'equation')
          {where_extra}
    """


async def vector_search(db, kb_id: str, query: str, top_k: int, vector_threshold: float, include_content: bool):
    count = db.execute(
        text(
            """
            SELECT COUNT(*)
            FROM chunk_embeddings ce
            JOIN chunks c ON c.id = ce.chunk_id
            WHERE ce.knowledge_base_id = :kb_id
              AND ce.is_enabled = true
              AND ce.source_type = 'chunk'
              AND c.is_enabled = true
              AND c.status = 'ready'
              AND c.chunk_type IN ('text', 'table', 'image', 'image_ocr_text', 'equation')
            """
        ),
        {"kb_id": kb_id},
    ).scalar() or 0
    if count == 0:
        return [], "当前知识库尚未生成向量，请先生成向量"

    vector = await embed_text(query, db=db)
    active_store = get_active_vector_config(db)
    external_message = ""
    if is_external_provider(active_store):
        hits, external_message = search_external_vector(db, kb_id, vector, top_k * 5)
        if hits:
            score_map = {hit.chunk_id: hit.score for hit in hits}
            rank_map = {hit.chunk_id: idx for idx, hit in enumerate(hits, start=1)}
            chunk_ids = list(score_map.keys())
            sql = _base_select_sql(
                ":zero_score AS vector_score",
                "LEFT JOIN chunk_embeddings ce ON ce.chunk_id = c.id AND ce.is_enabled = true AND ce.source_type = 'chunk'",
                "AND c.id = ANY(CAST(:chunk_ids AS text[]))",
            )
            rows = db.execute(text(sql), {"kb_id": kb_id, "chunk_ids": chunk_ids, "zero_score": 0.0}).all()
            results = []
            for row in rows:
                item = _row_to_result(row, "vector", include_content, "vector_score")
                item["vector_score"] = float(score_map.get(item["chunk_id"], 0))
                item["score"] = item["vector_score"]
                item["vector_rank"] = rank_map.get(item["chunk_id"])
                item["vector_store_provider"] = active_store.provider
                if item["vector_score"] >= vector_threshold:
                    results.append(item)
            results.sort(key=lambda r: (r.get("vector_rank") or 999999))
            return results, external_message
        if external_message and active_store and not bool((active_store.config or {}).get("fallback_to_postgres", True)):
            return [], external_message

    sql = _base_select_sql(
        "1 - (ce.embedding <=> CAST(:embedding AS vector)) AS vector_score",
        "JOIN chunk_embeddings ce ON ce.chunk_id = c.id",
        "AND ce.is_enabled = true AND ce.source_type = 'chunk'",
    ) + " ORDER BY ce.embedding <=> CAST(:embedding AS vector) LIMIT :limit"
    rows = db.execute(
        text(sql),
        {"kb_id": kb_id, "embedding": embedding_to_vector_literal(vector), "limit": top_k * 5},
    ).all()
    results = []
    for rank, row in enumerate(rows, start=1):
        item = _row_to_result(row, "vector", include_content, "vector_score")
        item["vector_score"] = item["score"]
        item["vector_rank"] = rank
        item["vector_store_provider"] = active_store.provider if active_store else "postgres"
        if item["vector_score"] >= vector_threshold:
            results.append(item)
    return results, external_message


def _sql_keyword_search_fallback(db, kb_id: str, query: str, top_k: int, keyword_threshold: float, include_content: bool):
    """Legacy fallback for emergency only.

    Disabled by default.  Set ELASTICSEARCH_FALLBACK_TO_SQL=true if you want
    the old ILIKE behavior when Elasticsearch is unavailable.
    """
    if not query.strip():
        return []
    sql = _base_select_sql(
        """
        LEAST(
            1.0,
            GREATEST(
                0.2,
                (
                    LENGTH(LOWER(COALESCE(NULLIF(ce.content, ''), c.content))) -
                    LENGTH(REPLACE(LOWER(COALESCE(NULLIF(ce.content, ''), c.content)), LOWER(:query), ''))
                )::float / GREATEST(LENGTH(:query), 1)::float / 5.0
            )
        ) AS keyword_score
        """,
        """
        LEFT JOIN chunk_embeddings ce
          ON ce.chunk_id = c.id
         AND ce.source_type = 'chunk'
         AND ce.is_enabled = true
        """,
        "AND COALESCE(NULLIF(ce.content, ''), c.content) ILIKE :pattern",
    ) + " ORDER BY keyword_score DESC, c.chunk_index ASC LIMIT :limit"
    rows = db.execute(
        text(sql),
        {"kb_id": kb_id, "query": query, "pattern": f"%{query}%", "limit": top_k * 5},
    ).all()
    results = []
    for rank, row in enumerate(rows, start=1):
        item = _row_to_result(row, "keyword", include_content, "keyword_score")
        item["keyword_score"] = item["score"]
        item["keyword_rank"] = rank
        item["keyword_backend"] = "sql_ilike_fallback"
        if item["keyword_score"] >= keyword_threshold:
            results.append(item)
    return results


def keyword_search(db, kb_id: str, query: str, top_k: int, keyword_threshold: float, include_content: bool):
    """BM25 keyword retrieval via Elasticsearch.

    This replaces the previous SQL ILIKE substring search.  Elasticsearch stores
    an inverted index over content/title/context/OCR/VLM/table fields and uses
    BM25 to score matches.  Scores returned here are normalized by the max ES
    score in this query so the existing 0-1 threshold UI still works.
    """
    if not query.strip():
        return []
    try:
        from app.services.keyword_index_service import search_bm25

        hits = search_bm25(kb_id, query, top_k)
    except ElasticsearchQueryComplexityError:
        raise
    except Exception as exc:
        if bool(getattr(settings, "elasticsearch_fallback_to_sql", False)):
            return _sql_keyword_search_fallback(db, kb_id, query, top_k, keyword_threshold, include_content)
        raise RuntimeError(f"Elasticsearch BM25 关键词检索失败：{exc}") from exc

    if not hits:
        return []

    hit_map = {hit.chunk_id: hit for hit in hits}
    chunk_ids = list(hit_map.keys())
    sql = _base_select_sql(
        ":zero_score AS keyword_score",
        """
        LEFT JOIN chunk_embeddings ce
          ON ce.chunk_id = c.id
         AND ce.source_type = 'chunk'
         AND ce.is_enabled = true
        """,
        "AND c.id = ANY(CAST(:chunk_ids AS text[]))",
    )
    rows = db.execute(text(sql), {"kb_id": kb_id, "chunk_ids": chunk_ids, "zero_score": 0.0}).all()
    row_map = {row.chunk_id: row for row in rows}

    results = []
    for hit in hits:
        row = row_map.get(hit.chunk_id)
        if row is None:
            continue
        item = _row_to_result(row, "keyword", include_content, "keyword_score")
        item["keyword_score"] = float(hit.score)
        item["keyword_raw_score"] = float(hit.raw_score)
        item["keyword_rank"] = hit.rank
        item["keyword_backend"] = "elasticsearch_bm25"
        if hit.highlight:
            item["matched_content"] = hit.highlight[:1000]
        item["score"] = item["keyword_score"]
        if item["keyword_score"] >= keyword_threshold:
            results.append(item)
    return results


def merge_results_rrf(vector_results: list[dict], keyword_results: list[dict], options: SearchOptions) -> list[dict]:
    merged: dict[str, dict] = {}
    for item in vector_results:
        entry = merged.setdefault(item["chunk_id"], item.copy())
        entry["vector_score"] = max(float(entry.get("vector_score") or 0), float(item.get("vector_score") or 0))
        entry["vector_rank"] = item.get("vector_rank")
    for item in keyword_results:
        entry = merged.setdefault(item["chunk_id"], item.copy())
        entry["keyword_score"] = max(float(entry.get("keyword_score") or 0), float(item.get("keyword_score") or 0))
        entry["keyword_rank"] = item.get("keyword_rank")
        for key in ("content", "preview", "matched_content"):
            if not entry.get(key) and item.get(key):
                entry[key] = item[key]

    results = []
    for item in merged.values():
        vector_rank = item.get("vector_rank")
        keyword_rank = item.get("keyword_rank")
        rrf_score = 0.0
        if vector_rank:
            rrf_score += options.vector_weight / (options.rrf_k + int(vector_rank))
        if keyword_rank:
            rrf_score += options.keyword_weight / (options.rrf_k + int(keyword_rank))
        item["rrf_score"] = rrf_score
        item["score"] = rrf_score
        item["match_type"] = "hybrid" if vector_rank and keyword_rank else "vector" if vector_rank else "keyword"
        results.append(item)
    return sorted(results, key=lambda value: value["score"], reverse=True)[: options.top_k]


def merge_results_weighted(vector_results: list[dict], keyword_results: list[dict], options: SearchOptions) -> list[dict]:
    merged: dict[str, dict] = {}
    for item in vector_results:
        entry = merged.setdefault(item["chunk_id"], item.copy())
        entry["vector_score"] = max(float(entry.get("vector_score") or 0), float(item.get("vector_score") or 0))
        entry["vector_rank"] = item.get("vector_rank")
    for item in keyword_results:
        entry = merged.setdefault(item["chunk_id"], item.copy())
        entry["keyword_score"] = max(float(entry.get("keyword_score") or 0), float(item.get("keyword_score") or 0))
        entry["keyword_rank"] = item.get("keyword_rank")
    results = []
    for item in merged.values():
        vector_score = float(item.get("vector_score") or 0)
        keyword_score = float(item.get("keyword_score") or 0)
        if vector_score and keyword_score:
            final_score = vector_score * settings.hybrid_vector_weight + keyword_score * settings.hybrid_keyword_weight
            match_type = "hybrid"
        elif vector_score:
            final_score = vector_score
            match_type = "vector"
        else:
            final_score = keyword_score
            match_type = "keyword"
        item["score"] = final_score
        item["rrf_score"] = 0.0
        item["match_type"] = match_type
        results.append(item)
    return sorted(results, key=lambda value: value["score"], reverse=True)[: options.top_k]


def merge_results(vector_results: list[dict], keyword_results: list[dict], options: SearchOptions) -> list[dict]:
    if options.fusion_strategy == "weighted":
        return merge_results_weighted(vector_results, keyword_results, options)
    return merge_results_rrf(vector_results, keyword_results, options)


def _safe_positive_int(value: Any, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    parsed = max(parsed, minimum)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def _context_text(value: str | None, max_chars: int) -> tuple[str, bool]:
    text_value = value or ""
    if max_chars <= 0 or len(text_value) <= max_chars:
        return text_value, False
    return text_value[:max_chars].rstrip(), True


def _clean_display_text(value: str | None, *, max_chars: int | None = None) -> str:
    """Return a UI-friendly version of chunk text without changing indexed content.

    Parent chunks intentionally keep full context for retrieval/LLM usage, but
    their raw content may include HTML table tags, image filenames, markdown
    fragments, equation wrappers and page boilerplate.  This helper is only for
    display payloads returned by retrieval APIs; it does not affect database
    content, embedding text, BM25 indexing, or LLM context.
    """
    text_value = unescape(str(value or ""))
    if not text_value.strip():
        return ""

    # Keep table cell text but remove markup.
    text_value = re.sub(r"<\s*br\s*/?\s*>", "\n", text_value, flags=re.I)
    text_value = re.sub(r"</\s*(p|div|tr|li|h[1-6])\s*>", "\n", text_value, flags=re.I)
    text_value = re.sub(r"<[^>]+>", " ", text_value)

    cleaned_lines: list[str] = []
    for raw_line in text_value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Drop low-value file-only lines frequently generated for image chunks.
        if re.match(r"^(文件名|file\s*name|filename)[:：]\s*.+\.(png|jpe?g|webp|bmp|gif)$", line, flags=re.I):
            continue
        if re.match(r"^!\[[^\]]*\]\([^)]*\)$", line):
            continue
        if re.match(r"^images?[/\\].+\.(png|jpe?g|webp|bmp|gif)$", line, flags=re.I):
            continue
        # Drop obvious page boilerplate lines, but keep real page footnotes.
        if re.match(r"^(页码|page\s*number)[:：]?\s*\d+$", line, flags=re.I):
            continue
        cleaned_lines.append(line)

    text_value = "\n".join(cleaned_lines)

    # Compress noisy LaTeX wrappers while preserving the fact that a formula exists.
    text_value = re.sub(r"\$\$\s*", "公式：", text_value)
    text_value = re.sub(r"\s*\$\$", "", text_value)
    text_value = re.sub(r"\\begin\{[^}]+\}|\\end\{[^}]+\}", " ", text_value)

    # Collapse excessive whitespace but keep paragraph breaks.
    text_value = re.sub(r"[ \t\r\f\v]+", " ", text_value)
    text_value = re.sub(r"\n{3,}", "\n\n", text_value).strip()
    if max_chars is not None and max_chars > 0 and len(text_value) > max_chars:
        return text_value[:max_chars].rstrip()
    return text_value


def _image_url_from_info(image_info: Any, metadata: dict | None = None) -> str:
    """Resolve an image URL for retrieval UI payloads."""
    metadata = metadata or {}
    candidates: list[Any] = []
    if isinstance(image_info, dict):
        candidates.extend([
            image_info.get("url"),
            image_info.get("image_url"),
            image_info.get("public_url"),
            image_info.get("relative_path"),
            image_info.get("path"),
        ])
    if isinstance(metadata, dict):
        nested = metadata.get("image_info")
        if isinstance(nested, dict):
            candidates.extend([
                nested.get("url"),
                nested.get("image_url"),
                nested.get("public_url"),
                nested.get("relative_path"),
                nested.get("path"),
            ])
        candidates.extend([metadata.get("image_url"), metadata.get("url"), metadata.get("relative_path"), metadata.get("path")])

    for value in candidates:
        raw = str(value or "").strip()
        if not raw:
            continue
        if raw.startswith(("http://", "https://", "/files/", "data:")):
            return raw
        if raw.startswith("minio://"):
            continue
        if raw.startswith("kb/"):
            return f"/files/{raw}"
    return ""


def _locate_child_in_parent(parent_content: str, child_content: str) -> tuple[int, int] | None:
    """Return (start, end) position of child_content inside parent_content.

    Uses progressively relaxed matching strategies.  Returns None when the
    child cannot be reliably located.
    """
    if not parent_content or not child_content:
        return None

    # Normalise whitespace for robust matching.
    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()

    p_norm = _norm(parent_content)
    c_norm = _norm(child_content)

    # Child content often carries a leading document-title line that is NOT
    # present in the parent.  Try progressively shorter suffixes to skip it.
    # Strategy 1 — full child (up to 300 chars)
    for needle_len in (300, 200, 120, 80):
        needle = c_norm[:needle_len] if len(c_norm) > needle_len else c_norm
        pos = p_norm.find(needle)
        if pos >= 0:
            return (pos, pos + len(needle))

    # Strategy 2 — skip the first "line" (often a title) and retry.
    if "\n" in child_content:
        after_first_line = child_content.split("\n", 1)[1]
        c2 = _norm(after_first_line)
        for needle_len in (200, 120, 80):
            needle = c2[:needle_len] if len(c2) > needle_len else c2
            pos = p_norm.find(needle)
            if pos >= 0:
                return (pos, pos + len(needle))

    # Strategy 3 — skip first two lines and retry.
    parts = child_content.split("\n")
    if len(parts) >= 3:
        after_two_lines = "\n".join(parts[2:])
        c3 = _norm(after_two_lines)
        for needle_len in (120, 80):
            needle = c3[:needle_len] if len(c3) > needle_len else c3
            pos = p_norm.find(needle)
            if pos >= 0:
                return (pos, pos + len(needle))

    # Strategy 4 — longest contiguous sentence (≥ 40 chars).
    sentences = re.split(r"[。！？\.!\?;；\n]+", c_norm)
    longest = ""
    for s in sentences:
        s = s.strip()
        if len(s) > len(longest):
            longest = s
    if len(longest) >= 40:
        pos = p_norm.find(longest)
        if pos >= 0:
            return (pos, pos + len(longest))

    # Strategy 5 — first 50 chars of longest sentence.
    if len(longest) >= 50:
        needle5 = longest[:50]
        pos = p_norm.find(needle5)
        if pos >= 0:
            return (pos, pos + len(needle5))

    return None


def _adjust_window_to_sentence_boundary(
    text: str, start: int, end: int, *, max_extend: int = 200
) -> tuple[int, int]:
    """Push *start* backward to a sentence/paragraph boundary and *end* forward.

    Returns adjusted (start, end) within ``[0, len(text)]``.
    """
    if not text:
        return (start, end)

    # ── start: move back to nearest paragraph break or sentence-end ──────
    search_start = max(start - max_extend, 0)
    prefix = text[search_start:start]
    # Prefer paragraph break.
    para_match = None
    for m in re.finditer(r"\n\s*\n", prefix):
        para_match = m
    if para_match is not None:
        new_start = search_start + para_match.end()
    else:
        # Fall back to sentence-ending punctuation followed by whitespace.
        sent_match = None
        for m in re.finditer(r"[。！？\.!\?;；]\s*", prefix):
            sent_match = m
        if sent_match is not None:
            new_start = search_start + sent_match.end()
        else:
            # At least try to start at a space.
            space_match = None
            for m in re.finditer(r"\s", prefix):
                space_match = m
            new_start = search_start + space_match.end() if space_match is not None else start

    # ── end: move forward to nearest sentence end ────────────────────────
    search_end = min(end + max_extend, len(text))
    suffix = text[end:search_end]
    sent_match = re.search(r"[。！？\.!\?;；]", suffix)
    if sent_match is not None:
        new_end = end + sent_match.end()
    else:
        # Try paragraph break.
        para_match2 = re.search(r"\n\s*\n", suffix)
        if para_match2 is not None:
            new_end = end + para_match2.start()
        else:
            new_end = end

    # Clamp.
    new_start = max(0, min(new_start, len(text)))
    new_end = max(new_end, new_start)
    new_end = min(new_end, len(text))
    return (new_start, new_end)


def _parent_content_raw(db, parent_id: str) -> str:
    """Return the *full* parent chunk content, without any truncation."""
    row = db.execute(
        text("SELECT content FROM chunks WHERE id = :id"),
        {"id": parent_id},
    ).first()
    return (row.content or "") if row is not None else ""


def _parent_window_preview(
    parent_content: str,
    matched_children: list[dict],
    max_chars: int,
    neighbor_chars: int = 300,
) -> dict:
    """Build a parent-context window centred around matched child hits.

    Returns a dict compatible with the existing ``_chunk_preview`` shape,
    extended with window metadata fields.
    """
    content_len = len(parent_content)

    # ── full parent (no truncation needed) ───────────────────────────────
    if content_len <= max_chars:
        return {
            "content": parent_content,
            "returned_chars": content_len,
            "content_length": content_len,
            "window_mode": "full_parent",
            "window_start": 0,
            "window_end": content_len,
            "hit_start": None,
            "hit_end": None,
            "is_truncated": False,
            "locate_failed": False,
            "matched_child_count": len(matched_children),
        }

    # ── gather hit positions ─────────────────────────────────────────────
    hits: list[tuple[int, int]] = []
    locate_failed = True
    for child in matched_children:
        child_content = child.get("content") or child.get("matched_content") or ""
        if not child_content:
            continue
        loc = _locate_child_in_parent(parent_content, child_content)
        if loc is not None:
            hits.append(loc)
            locate_failed = False

    # ── no hits found → fallback to head truncation ─────────────────────
    if not hits:
        clipped = parent_content[:max_chars].rstrip()
        return {
            "content": clipped,
            "returned_chars": len(clipped),
            "content_length": content_len,
            "window_mode": "head_fallback",
            "window_start": 0,
            "window_end": len(clipped),
            "hit_start": None,
            "hit_end": None,
            "is_truncated": content_len > max_chars,
            "locate_failed": True,
            "matched_child_count": len(matched_children),
        }

    # ── compute hit span ─────────────────────────────────────────────────
    hit_start = min(h[0] for h in hits)
    hit_end = max(h[1] for h in hits)
    hit_span = hit_end - hit_start

    multi_hit_overflow = False
    if len(hits) > 1 and hit_span > max_chars:
        # Keep only the first (best-scoring) hit.
        hit_start = hits[0][0]
        hit_end = hits[0][1]
        hit_span = hit_end - hit_start
        multi_hit_overflow = True

    # ── calculate window ─────────────────────────────────────────────────
    if hit_span >= max_chars:
        # Hit span is already at or above limit — start from hit_start.
        window_start = hit_start
        window_end = min(hit_start + max_chars, content_len)
    else:
        # Distribute remaining budget around the hit.
        remaining = max_chars - hit_span
        before_budget = min(remaining // 2, max(neighbor_chars, remaining // 2))
        after_budget = remaining - before_budget

        window_start = max(hit_start - before_budget, 0)
        window_end = min(hit_end + after_budget, content_len)

        # If we hit a boundary, shift the extra to the other side.
        slack_left = hit_start - before_budget
        if slack_left < 0:
            # Couldn't go back enough — extend forward.
            window_end = min(window_end + abs(slack_left), content_len)
        slack_right = (hit_end + after_budget) - content_len
        if slack_right > 0:
            # Couldn't go forward enough — extend backward.
            window_start = max(window_start - slack_right, 0)

    # ── snap to sentence boundaries ──────────────────────────────────────
    window_start, window_end = _adjust_window_to_sentence_boundary(
        parent_content, window_start, window_end
    )
    # Enforce max_chars on the adjusted window.
    if window_end - window_start > max_chars:
        window_end = min(window_start + max_chars, content_len)

    window_content = parent_content[window_start:window_end]
    is_truncated = (window_start > 0) or (window_end < content_len)

    result: dict = {
        "content": window_content,
        "returned_chars": len(window_content),
        "content_length": content_len,
        "window_mode": "around_hit",
        "window_start": window_start,
        "window_end": window_end,
        "hit_start": hit_start,
        "hit_end": hit_end,
        "is_truncated": is_truncated,
        "locate_failed": False,
        "matched_child_count": len(matched_children),
    }
    if multi_hit_overflow:
        result["multi_hit_overflow"] = True
    return result


def _chunk_preview(db, chunk_id: str | None, *, max_chars: int = 300, include_metadata: bool = True) -> dict | None:
    if not chunk_id:
        return None
    row = db.execute(
        text(
            """
            SELECT id, content, chunk_type, context_header, chunk_index,
                   parent_chunk_id, image_info, metadata_json
            FROM chunks
            WHERE id = :id
            """
        ),
        {"id": chunk_id},
    ).first()
    if row is None:
        return None
    content = row.content or ""
    clipped, truncated = _context_text(content, max_chars)
    display_content = _clean_display_text(clipped)
    payload = {
        "chunk_id": row.id,
        "chunk_type": row.chunk_type,
        "chunk_index": row.chunk_index,
        "parent_chunk_id": row.parent_chunk_id,
        "context_header": row.context_header or "",
        "content": clipped,
        "content_preview": clipped[:300],
        "display_content": display_content,
        "display_preview": display_content[:300],
        "content_length": len(content),
        "returned_chars": len(clipped),
        "display_chars": len(display_content),
        "preview_chars": max_chars,
        "is_truncated": truncated,
    }
    if include_metadata:
        image_info = row.image_info or {}
        metadata = row.metadata_json or {}
        payload["image_info"] = image_info
        payload["image_url"] = _image_url_from_info(image_info, metadata)
        payload["metadata"] = metadata
        payload["metadata_json"] = metadata
    return payload


def _compact_child_from_result(item: dict) -> dict:
    raw_content = item.get("matched_content") or item.get("preview") or item.get("content") or ""
    metadata = item.get("metadata") or item.get("metadata_json") or {}
    image_info = item.get("image_info") or {}
    display_content = _clean_display_text(raw_content)
    return {
        "chunk_id": item.get("chunk_id"),
        "document_id": item.get("document_id"),
        "chunk_type": item.get("chunk_type"),
        "chunk_index": item.get("chunk_index"),
        "context_header": item.get("context_header") or "",
        "matched_content": raw_content,
        "content_preview": raw_content[:300],
        "display_content": display_content,
        "display_preview": display_content[:300],
        "score": float(item.get("score") or 0),
        "rrf_score": float(item.get("rrf_score") or 0),
        "vector_score": float(item.get("vector_score") or 0),
        "keyword_score": float(item.get("keyword_score") or 0),
        "vector_rank": item.get("vector_rank"),
        "keyword_rank": item.get("keyword_rank"),
        "match_type": item.get("match_type"),
        "metadata": metadata,
        "metadata_json": metadata,
        "image_info": image_info,
        "image_url": _image_url_from_info(image_info, metadata),
    }


def _row_child_summary(row, *, relation: str = "related", is_matched: bool = False) -> dict:
    content = row.content or ""
    metadata = row.metadata_json or {}
    image_info = row.image_info or {}
    display_content = _clean_display_text(content, max_chars=600)
    return {
        "chunk_id": row.id,
        "chunk_type": row.chunk_type,
        "chunk_index": row.chunk_index,
        "context_header": row.context_header or "",
        "content_preview": content[:300],
        "display_preview": display_content[:300],
        "content_length": len(content),
        "relation": relation,
        "is_matched": is_matched,
        "image_info": image_info,
        "image_url": _image_url_from_info(image_info, metadata),
        "metadata": metadata,
        "metadata_json": metadata,
    }


def _metadata_truthy(metadata: dict | None, key: str) -> bool:
    if not isinstance(metadata, dict):
        return False
    value = metadata.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _parent_children_summary(
    db,
    parent_chunk_id: str | None,
    *,
    matched_chunk_ids: set[str] | None = None,
    matched_chunk_indexes: set[int] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Return relevant children of a parent, not all children blindly.

    Priority:
    1. matched children,
    2. previous/next siblings of matched children,
    3. visual/table/equation children,
    4. remaining children by document order until limit.
    """
    if not parent_chunk_id:
        return []
    if limit is None:
        limit = _safe_positive_int(getattr(settings, "retrieval_parent_children_limit", 30), 30, minimum=0, maximum=500)
    else:
        limit = _safe_positive_int(limit, 30, minimum=0, maximum=500)
    if limit <= 0:
        return []

    rows = db.execute(
        text(
            """
            SELECT id, content, chunk_type, chunk_index, context_header, image_info, metadata_json
            FROM chunks
            WHERE parent_chunk_id = :parent_id
              AND is_enabled = true
              AND status = 'ready'
            ORDER BY chunk_index ASC
            LIMIT 1000
            """
        ),
        {"parent_id": parent_chunk_id},
    ).all()
    if not rows:
        return []

    matched_chunk_ids = matched_chunk_ids or set()
    matched_chunk_indexes = matched_chunk_indexes or set()
    neighbor_indexes = set()
    for idx in matched_chunk_indexes:
        if idx is not None:
            neighbor_indexes.add(idx - 1)
            neighbor_indexes.add(idx + 1)

    selected: dict[str, tuple[int, str, Any]] = {}
    for row in rows:
        metadata = row.metadata_json or {}
        is_matched = row.id in matched_chunk_ids
        is_neighbor = row.chunk_index in neighbor_indexes
        is_equation = _metadata_truthy(metadata, "is_equation") or str(metadata.get("subtype") or "").lower() in {"equation", "formula"}
        is_visual_or_table = row.chunk_type in {"image", "table"} or bool(metadata.get("visual_subtype"))
        if is_matched:
            priority, relation = 0, "matched"
        elif is_neighbor:
            priority, relation = 1, "neighbor"
        elif is_visual_or_table:
            priority, relation = 2, "visual_or_table"
        elif is_equation:
            priority, relation = 2, "equation"
        else:
            priority, relation = 3, "same_parent"
        selected[row.id] = (priority, relation, row)

    ordered = sorted(selected.values(), key=lambda item: (item[0], item[2].chunk_index or 0))
    children = []
    for priority, relation, row in ordered[:limit]:
        children.append(_row_child_summary(row, relation=relation, is_matched=(row.id in matched_chunk_ids)))
    return children


def attach_context(db, results: list[dict], options: SearchOptions | None = None) -> None:
    parent_chars = _safe_positive_int(
        options.parent_context_max_chars if options else getattr(settings, "retrieval_parent_context_max_chars", 2000),
        2000,
        minimum=0,
        maximum=50000,
    )
    neighbor_chars = _safe_positive_int(
        options.neighbor_context_max_chars if options else getattr(settings, "retrieval_neighbor_context_max_chars", 300),
        300,
        minimum=0,
        maximum=20000,
    )
    children_limit = _safe_positive_int(
        options.parent_children_limit if options else getattr(settings, "retrieval_parent_children_limit", 30),
        30,
        minimum=0,
        maximum=500,
    )
    include_parent_context = True if options is None else bool(options.include_parent_context)
    for item in results:
        try:
            parent_id = item.get("parent_chunk_id")
            parent = None
            if include_parent_context and parent_id:
                parent = _chunk_preview(db, parent_id, max_chars=parent_chars)
                # Build window-based context when child content is available.
                child_content = item.get("content") or item.get("matched_content") or ""
                parent_full = _parent_content_raw(db, parent_id)
                if child_content and len(parent_full) > parent_chars:
                    window = _parent_window_preview(
                        parent_full,
                        [{"content": child_content}],
                        parent_chars,
                        neighbor_chars,
                    )
                    # Merge window fields into the parent preview dict.
                    parent["content"] = window["content"]
                    parent["returned_chars"] = window["returned_chars"]
                    parent["window_mode"] = window["window_mode"]
                    parent["window_start"] = window["window_start"]
                    parent["window_end"] = window["window_end"]
                    parent["hit_start"] = window["hit_start"]
                    parent["hit_end"] = window["hit_end"]
                    parent["is_truncated"] = window["is_truncated"]
                    parent["locate_failed"] = window.get("locate_failed", False)
                    parent["matched_child_count"] = window.get("matched_child_count", 1)
            matched_indexes = {item.get("chunk_index")} if item.get("chunk_index") is not None else set()
            item["context"] = {
                "parent": parent,
                "previous": _chunk_preview(db, item.get("pre_chunk_id"), max_chars=neighbor_chars),
                "next": _chunk_preview(db, item.get("next_chunk_id"), max_chars=neighbor_chars),
                "parent_children": _parent_children_summary(
                    db,
                    parent_id,
                    matched_chunk_ids={item.get("chunk_id")} if item.get("chunk_id") else set(),
                    matched_chunk_indexes=matched_indexes,
                    limit=children_limit,
                ) if include_parent_context else [],
            }
            item["parent_context"] = parent
        except Exception:
            item["context"] = None
            item["parent_context"] = None


def build_parent_groups(db, results: list[dict], options: SearchOptions) -> list[dict]:
    """Group child hits by parent_chunk_id and return configurable parent contexts.

    Existing flat `results` are kept for backward compatibility.  This grouped
    view implements the recommended parent-child retrieval logic: child hits are
    precise recall units; parent chunks are context containers returned once per
    parent.
    """
    if not options.include_parent_context or options.max_parent_contexts <= 0:
        return []

    buckets: dict[str, dict] = {}
    for item in results:
        parent_id = item.get("parent_chunk_id") or f"orphan:{item.get('chunk_id')}"
        bucket = buckets.setdefault(parent_id, {"parent_chunk_id": item.get("parent_chunk_id"), "children": []})
        bucket["children"].append(item)

    bonus = _float_payload(getattr(settings, "retrieval_parent_group_hit_bonus", 0.05), 0.05, minimum=0, maximum=10)
    groups: list[dict] = []
    for parent_key, bucket in buckets.items():
        matched_children = sorted(bucket["children"], key=lambda x: float(x.get("score") or 0), reverse=True)
        max_child_score = max(float(child.get("score") or 0) for child in matched_children) if matched_children else 0.0
        parent_score = max_child_score + bonus * log1p(len(matched_children))
        parent_id = bucket.get("parent_chunk_id")
        matched_ids = {child.get("chunk_id") for child in matched_children if child.get("chunk_id")}
        matched_indexes = {child.get("chunk_index") for child in matched_children if child.get("chunk_index") is not None}

        # Build parent context with window-based preview.
        parent_context = None
        if parent_id:
            parent_context = _chunk_preview(db, parent_id, max_chars=options.parent_context_max_chars)
            parent_full = _parent_content_raw(db, parent_id)
            if len(parent_full) > options.parent_context_max_chars:
                window = _parent_window_preview(
                    parent_full,
                    [_compact_child_from_result(child) for child in matched_children],
                    options.parent_context_max_chars,
                    options.neighbor_context_max_chars,
                )
                parent_context["content"] = window["content"]
                parent_context["returned_chars"] = window["returned_chars"]
                parent_context["window_mode"] = window["window_mode"]
                parent_context["window_start"] = window["window_start"]
                parent_context["window_end"] = window["window_end"]
                parent_context["hit_start"] = window["hit_start"]
                parent_context["hit_end"] = window["hit_end"]
                parent_context["is_truncated"] = window["is_truncated"]
                parent_context["locate_failed"] = window.get("locate_failed", False)
                parent_context["matched_child_count"] = window.get("matched_child_count", len(matched_children))
                parent_context["multi_hit_overflow"] = window.get("multi_hit_overflow", False)
        parent_children = _parent_children_summary(
            db,
            parent_id,
            matched_chunk_ids=matched_ids,
            matched_chunk_indexes=matched_indexes,
            limit=options.parent_children_limit,
        ) if parent_id else []
        groups.append(
            {
                "parent_key": parent_key,
                "parent_chunk_id": parent_id,
                "parent_context": parent_context,
                "score": parent_score,
                "max_child_score": max_child_score,
                "matched_child_count": len(matched_children),
                "matched_children": [_compact_child_from_result(child) for child in matched_children],
                "parent_children": parent_children,
                "is_orphan": parent_id is None,
            }
        )

    groups.sort(key=lambda group: group["score"], reverse=True)
    return groups[: options.max_parent_contexts]




def _expand_figure_bindings(
    db: Session,
    results: list[dict],
    *,
    enabled: bool = True,
    degradation_messages: list[str] | None = None,
) -> list[dict]:
    """Compatibility wrapper for Phase 1 scoped expansion."""
    if not enabled:
        return results
    return expand_figure_bindings(
        db,
        results,
        degradation_messages=degradation_messages,
    )


async def hybrid_search(db, actor: User | None, kb_id: str, payload: dict):
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        return error_response("知识库不存在")
    options, error = normalize_options(db, payload)
    if error:
        return error_response(error)

    vector_results: list[dict] = []
    keyword_results: list[dict] = []
    vector_message = ""
    vector_available = False
    degradation_messages: list[str] = []
    if options.use_vector:
        try:
            vector_results, vector_message = await vector_search(
                db, kb_id, options.query, options.top_k, options.vector_threshold, options.include_content
            )
            # An empty result with a message means the vector path was not
            # available (for example, the knowledge base has no embeddings or
            # an external store failed with PostgreSQL fallback disabled).
            vector_available = bool(vector_results) or not vector_message
        except Exception as exc:
            logger.warning("Vector retrieval failed while hybrid search continues: kb_id=%s error=%s", kb_id, exc)
            if not options.use_keyword:
                return error_response(str(exc) or "向量检索失败")
            degradation_messages.append(f"向量检索失败，已继续使用关键词检索：{exc}")

    if options.use_keyword:
        try:
            keyword_results = keyword_search(
                db, kb_id, options.query, options.top_k, options.keyword_threshold, options.include_content
            )
        except ElasticsearchQueryComplexityError as exc:
            if not (options.use_vector and vector_available):
                vector_detail = f"；{vector_message}" if vector_message else ""
                return error_response(f"Elasticsearch BM25 查询过于复杂：{exc}{vector_detail}")
            logger.warning(
                "BM25 retrieval degraded after safe query retry; keeping vector results: "
                "kb_id=%s query_length=%s",
                kb_id,
                len(options.query),
            )
            degradation_messages.append("关键词检索超过 Elasticsearch 查询复杂度限制，已降级为向量检索结果")
        except Exception as exc:
            return error_response(str(exc) or "关键词检索失败")

    results = merge_results(vector_results, keyword_results, options)
    results = _expand_figure_bindings(
        db,
        results,
        enabled=settings.document_relation_retrieval_expansion_enabled,
        degradation_messages=degradation_messages,
    )
    parent_groups: list[dict] = []
    if options.include_context:
        attach_context(db, results, options)
        parent_groups = build_parent_groups(db, results, options) if options.merge_hits_by_parent else []
    if degradation_messages:
        message = "；".join(degradation_messages)
    elif options.use_vector and not vector_results and not keyword_results and vector_message:
        message = vector_message
    else:
        message = ""

    embedding_runtime = get_embedding_runtime(db)

    record_audit(
        db,
        actor,
        "hybrid_search",
        "knowledge_base",
        kb_id,
        request_summary={
            "knowledge_base_id": kb_id,
            "query": options.query[:100],
            "top_k": options.top_k,
            "fusion_strategy": options.fusion_strategy,
        },
        result_summary={
            "result_count": len(results),
            "fusion_strategy": options.fusion_strategy,
            "use_vector": options.use_vector,
            "use_keyword": options.use_keyword,
        },
    )
    db.commit()
    return success_response(
        {
            "query": options.query,
            "query_text": options.query,
            "results": results,
            "parent_groups": parent_groups,
            "grouped_results": parent_groups,
            "meta": {
                "top_k": options.top_k,
                "match_count": options.top_k,
                "fusion_strategy": options.fusion_strategy,
                "rrf_k": options.rrf_k,
                "vector_weight": options.vector_weight,
                "keyword_weight": options.keyword_weight,
                "vector_threshold": options.vector_threshold,
                "keyword_threshold": options.keyword_threshold,
                "use_vector": options.use_vector,
                "use_keyword": options.use_keyword,
                "disable_vector_match": not options.use_vector,
                "disable_keywords_match": not options.use_keyword,
                "include_context": options.include_context,
                "include_parent_context": options.include_parent_context,
                "parent_context_mode": options.parent_context_mode,
                "parent_context_max_chars": options.parent_context_max_chars,
                "neighbor_context_max_chars": options.neighbor_context_max_chars,
                "parent_children_limit": options.parent_children_limit,
                "max_parent_contexts": options.max_parent_contexts,
                "merge_hits_by_parent": options.merge_hits_by_parent,
                "parent_group_count": len(parent_groups),
                "embedding_provider": embedding_runtime.provider,
                "embedding_model": embedding_runtime.model_name,
            },
        },
        message,
    )


async def build_llm_context(db, actor: User | None, kb_id: str, payload: dict):
    """Build clean LLM-ready context via the unified context service.

    Kept as a backward-compatible wrapper so existing FastAPI routes and the
    current frontend do not need to change.  Future MCP/RAG chat adapters should
    call KnowledgeContextService directly or call the /llm-context HTTP endpoint.
    """
    from app.services.context.knowledge_context_service import build_knowledge_context

    return await build_knowledge_context(
        db=db,
        actor=actor,
        kb_id=kb_id,
        payload=payload,
    )
